import * as vscode from 'vscode';
import * as fs from 'node:fs/promises';
import * as path from 'node:path';
import {execFile} from 'node:child_process';
import {randomBytes,createHash} from 'node:crypto';
import {Client,Pairing,RemoteError} from './client';
import {startTransport,Transport} from './transport';
import {Modules,FrontendModule} from './modules';

type Item=Record<string,any>;
type Pending={commandId:string;accountId:string;conversationId:string;kind:string;text?:string;attachmentId?:string;
  createdAt?:number;clientTag?:string;
  upload?:{path:string;name:string;bytes:number;sha256:string;contentType:string;uploadKey:string}};
type Tracked={status:string;echo?:Pending;messageId?:string;nextCheck?:number};
const key=()=>randomBytes(16).toString('hex');
async function unprotect(data:Buffer):Promise<Buffer>{
  const command="Add-Type -AssemblyName System.Security; $taskBytes=[Convert]::FromBase64String([Console]::In.ReadToEnd()); $taskPlain=[Security.Cryptography.ProtectedData]::Unprotect($taskBytes,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser); [Console]::Write([Convert]::ToBase64String($taskPlain))";
  return new Promise((resolve,reject)=>{
    const process=execFile('powershell.exe',['-NoProfile','-NonInteractive','-Command',command],{windowsHide:true,maxBuffer:2*1024*1024},(error,stdout)=>error?reject(new Error('配对文件不能在当前 Windows 用户下解密')):resolve(Buffer.from(stdout.trim(),'base64')));
    process.stdin!.end(data.toString('base64'));
  });
}

class Workbench implements vscode.WebviewViewProvider,vscode.Disposable{
  private view?:vscode.WebviewView;private client?:Client;private state='unpaired';private detail='';
  private bootstrapped=false;private webviewReady=false;
  private transport?:Transport;
  private accounts=new Map<string,Item>();private contacts=new Map<string,Item>();private conversations=new Map<string,Item>();private messages=new Map<string,Item>();
  private selected?:Item;private selectionRequest:string|null=null;private platforms:Item[]=[];private cursor=0;private instance='';private nextBefore:Item|null|undefined;private historyGeneration=0;private loadingHistory=new Set<number>();
  private timer?:NodeJS.Timeout;private busy=false;private flushing=false;private pending:Pending[]=[];private persistChain=Promise.resolve();
  private media=new Map<string,Buffer>();private mediaInflight=new Map<string,Promise<Buffer>>();private tracked=new Map<string,Tracked>();
  private subscribed=false;private pushEnabled=false;private lastSync=0;private lastTracked=0;private lastPing=0;
  private sentContacts=false;private sentConversations=false;
  constructor(private context:vscode.ExtensionContext,private output:vscode.OutputChannel,private modules:Modules){}
  async initialize(){
    try{this.pending=JSON.parse(await this.context.secrets.get('outbox.v1')||'[]');}catch{this.detail='本地待发记录无法读取，请查看诊断';}
    try{this.tracked=new Map(JSON.parse(await this.context.secrets.get('tracked.v1')||'[]').map(([id,value]:[string,string|Tracked])=>[id,typeof value==='string'?{status:value}:value]));}catch{this.detail='发送回执列表无法读取，请查看诊断';}
    let pairing=await this.context.secrets.get('pairing.v1');
    const bootstrap=path.join(process.env.LOCALAPPDATA||this.context.globalStorageUri.fsPath,'ChatBridge','pairing.dpapi');
    if(!pairing){try{await this.importPairing(bootstrap,false);pairing=await this.context.secrets.get('pairing.v1');}catch(error:any){if(error.code!=='ENOENT')this.detail=error.message;}}
    if(pairing)this.connect(JSON.parse(pairing));else this.render();
    this.timer=setInterval(()=>void this.synchronize(),500);
  }
  private validatePairing(value:any):Pairing{
    if(!value||typeof value.host!=='string'||!Number.isInteger(value.port)||value.port<1||value.port>65535||
      typeof value.serverName!=='string'||typeof value.caPem!=='string'||typeof value.clientCertPem!=='string'||typeof value.clientKeyPem!=='string'||!/^[a-f0-9]{64}$/.test(value.serverFingerprint))throw new Error('配对文件格式无效');
    return value;
  }
  async importPairing(file?:string,connect=true){
    if(!file){const selected=await vscode.window.showOpenDialog({canSelectMany:false,filters:{'ChatBridge 配对':['dpapi']}});if(!selected)return;file=selected[0].fsPath;}
    const data=await fs.readFile(file);const value=this.validatePairing(JSON.parse((await unprotect(data)).toString('utf8')));
    await this.context.secrets.store('pairing.v1',JSON.stringify(value));
    // 导入完成后由编辑器保管密钥，避免长期留一份额外的配对文件。
    await fs.unlink(file);if(connect)this.connect(value);
  }
  private async connect(pairing:Pairing){
    this.client?.stop();this.transport?.close();
    try{this.transport=await startTransport(pairing,this.context.extensionUri.fsPath,path.join(process.env.LOCALAPPDATA||this.context.globalStorageUri.fsPath,'ChatBridge','transport'),code=>{this.detail=code;this.render();});}
    catch(error){this.fail(error);return;}
    const client=new Client(this.transport.pairing,this.transport.open);this.client=client;
    client.on('state',(state:string)=>{this.state=state;if(state!=='online')this.subscribed=false;this.render();});
    client.on('events',(result:any)=>{if(client===this.client&&this.bootstrapped)this.consume(result);});
    client.on('diagnostic',(code:string)=>{this.detail=code;this.output.appendLine(new Date().toISOString()+' '+code);this.render();});
    client.on('ready',(hello:any)=>void this.ready(hello).catch(error=>this.fail(error)));
    client.start();
  }
  reconnect(){this.client?.reconnect();}
  private async request(op:string,args:any={}):Promise<any>{
    if(!this.client)throw new RemoteError('UNPAIRED','请先导入设备配对');return this.client.request(op,args);
  }
  private async ready(hello:any){
    this.bootstrapped=false;this.subscribed=false;this.pushEnabled=hello.features?.eventPush===true;
    this.platforms=hello.platforms;this.instance=hello.instanceId;
    const saved=this.context.globalState.get<{instance:string;cursor:number}>('sync.cursor');
    // 首次进入已经读取完整快照，只补 hello 之后的变化，避免重放全部旧目录拖慢聊天。
    this.cursor=saved?.instance===this.instance&&saved.cursor>=0&&saved.cursor<=hello.latestCursor?saved.cursor:hello.latestCursor;
    this.sentContacts=false;this.sentConversations=false;
    this.accounts.clear();this.contacts.clear();this.conversations.clear();
    for(const account of (await this.request('accounts.list')).items)this.accounts.set(account.id,account);
    for(const [operation,map] of [['contacts.list',this.contacts],['conversations.list',this.conversations]] as const){
      let offset:number|null=0;
      while(offset!==null){const result=await this.request(operation,{offset});for(const item of result.items)map.set(item.id,item);offset=result.nextOffset;}
    }
    this.bootstrapped=true;this.detail='';this.render();await this.synchronize();
    if(this.pushEnabled){await this.request('sync.subscribe',{after:this.cursor});this.subscribed=true;}
    await this.flush();
  }
  private apply(event:any){
    const item=event.entity;
    if(event.type==='account.upsert')this.accounts.set(item.id,item);
    if(event.type==='contact.upsert'){this.contacts.set(item.id,item);this.sentContacts=false;}
    if(event.type==='conversation.upsert'){this.conversations.set(item.id,item);this.sentConversations=false;}
    if(event.type==='message.upsert'&&item.conversationId===this.selected?.id)this.messages.set(item.id,item);
  }
  private consume(result:any){
    let changed=false;
    for(const event of result.events){if(event.cursor>this.cursor){this.apply(event);this.cursor=event.cursor;changed=true;}}
    if(changed){void this.context.globalState.update('sync.cursor',{instance:this.instance,cursor:this.cursor});this.render();}
  }
  private async synchronize(){
    if(this.busy||!this.bootstrapped||!this.client?.connected||this.state!=='online')return;this.busy=true;
    try{
      const now=Date.now();
      if(!this.subscribed||now-this.lastSync>20000)for(let pages=0;pages<20;pages++){
        const result=await this.request('sync.events',{after:this.cursor});
        this.consume(result);this.lastSync=now;
        if(this.cursor>=result.latestCursor)break;
      }
      if(this.tracked.size&&now-this.lastTracked>=500){
        this.lastTracked=now;let dirty=false;
        for(const id of this.tracked.keys()){
          const tracked=this.tracked.get(id)!;if((tracked.nextCheck||0)>now)continue;
          const result=await this.request('commands.get',{commandId:id});dirty=dirty||tracked.status!==result.status||tracked.messageId!==result.messageId;tracked.status=result.status;tracked.messageId=result.messageId;tracked.nextCheck=now+(result.status==='uncertain'?15000:500);
          if(result.status==='serverAccepted'){
            if(this.selected?.id===tracked.echo?.conversationId&&!this.messages.has(result.messageId)){
              const cid=tracked.echo!.conversationId,generation=this.historyGeneration;
              const history=await this.request('messages.list',{conversationId:cid});
              if(this.selected?.id===cid&&this.historyGeneration===generation)for(const message of history.items)this.messages.set(message.id,message);
            }
            this.tracked.delete(id);dirty=true;
          }
        }
        if(dirty){await this.context.secrets.store('tracked.v1',JSON.stringify([...this.tracked]));this.render();}
      }
      if(now-this.lastPing>20000){this.lastPing=now;await this.request('ping');}
      await this.flush();
    }catch(error){this.fail(error);}finally{this.busy=false;}
  }
  private persist(){
    const snapshot=JSON.stringify(this.pending);
    this.persistChain=this.persistChain.catch(()=>{}).then(()=>this.context.secrets.store('outbox.v1',snapshot));return this.persistChain;
  }
  private async enqueue(value:Pending){
    if(this.pending.length>=50)throw new Error('待发队列已达 50 条，请先恢复连接');
    value.createdAt=Date.now();this.pending.push(value);
    try{await this.persist();}catch(error){this.pending=this.pending.filter(item=>item!==value);throw error;}
    this.render();void this.flush();
  }
  private async flush(){
    if(this.flushing||!this.client?.connected||this.state!=='online')return;this.flushing=true;
    try{
      for(const command of [...this.pending]){
        if(command.upload&&!command.attachmentId){
          const file=await fs.readFile(command.upload.path);
          if(file.length!==command.upload.bytes||createHash('sha256').update(file).digest('hex')!==command.upload.sha256)throw new Error('待发文件已改变，请取消后重新选择');
          const upload=await this.request('attachments.begin',command.upload);let offset=upload.received;
          while(offset<file.length){const result=await this.request('attachments.chunk',{uploadId:upload.uploadId,offset,data:file.subarray(offset,offset+32768).toString('base64')});offset=result.received;}
          const attachment=await this.request('attachments.commit',{uploadId:upload.uploadId});command.attachmentId=attachment.id;await this.persist();
        }
        const {upload,createdAt,clientTag,...request}=command;const result=await this.request('messages.send',request);
        // 先保存后续查账所需的编号，再移出待发队列，编辑器重启也能继续核对。
        if(result.status!=='serverAccepted')this.tracked.set(command.commandId,{status:result.status,echo:command,messageId:result.messageId});
        await this.context.secrets.store('tracked.v1',JSON.stringify([...this.tracked]));
        this.pending=this.pending.filter(item=>item.commandId!==command.commandId);await this.persist();
        if(result.status==='uncertain')this.notify('发送结果待核对，请勿重复提交');this.render();
      }
    }catch(error){this.fail(error);}finally{this.flushing=false;}
  }
  async select(id:string,requestId?:string){
    const selected=this.contacts.get(id)||this.conversations.get(id);if(!selected)return;
    // 回传页面请求号，避免返回列表后被迟到的选择响应重新打开对话。
    this.selected=selected;this.selectionRequest=requestId||null;this.historyGeneration++;this.messages.clear();this.nextBefore=undefined;this.render();await this.history();
  }
  private async history(){
    const generation=this.historyGeneration;
    if(!this.selected||this.nextBefore===null||this.loadingHistory.has(generation))return;this.loadingHistory.add(generation);
    try{
      const cid=this.selected.id;const result=await this.request('messages.list',{conversationId:cid,before:this.nextBefore});
      // 快速切走再切回同一会话时，旧请求也不能覆盖新一轮分页游标。
      if(this.selected?.id!==cid||generation!==this.historyGeneration)return;
      for(const message of result.items)this.messages.set(message.id,message);this.nextBefore=result.nextBefore;this.render();
    }finally{this.loadingHistory.delete(generation);}
  }
  private async attachment(info:Item):Promise<Buffer>{
    const cacheKey=info.id+':'+info.sha256;const cached=this.media.get(cacheKey);if(cached)return cached;
    if(this.mediaInflight.has(cacheKey))return this.mediaInflight.get(cacheKey)!;
    const operation=(async()=>{
      if(info.bytes>32*1024*1024)throw new Error('附件超过本版 32 MiB 上限');
      const parts:Buffer[]=[];let offset=0;
      while(offset<info.bytes){
        const offsets=[offset,offset+32768,offset+65536,offset+98304].filter(value=>value<info.bytes);
        const batch=await Promise.all(offsets.map(value=>this.request('attachments.read',{attachmentId:info.id,offset:value})));
        for(let index=0;index<batch.length;index++){
          const response=batch[index];const data=Buffer.from(response.data,'base64');
          if(response.offset!==offset||response.total!==info.bytes||response.sha256!==info.sha256||!data.length||response.nextOffset!==offset+data.length)throw new Error('附件分块校验失败');
          parts.push(data);offset+=data.length;
        }
      }
      const data=Buffer.concat(parts);
      if(createHash('sha256').update(data).digest('hex')!==info.sha256)throw new Error('附件完整性校验失败');
      this.media.set(cacheKey,data);
      while([...this.media.values()].reduce((n,value)=>n+value.length,0)>64*1024*1024)this.media.delete(this.media.keys().next().value!);
      return data;
    })();
    this.mediaInflight.set(cacheKey,operation);
    try{return await operation;}finally{this.mediaInflight.delete(cacheKey);}
  }
  private findAttachment(id:string):Item|undefined{
    for(const message of this.messages.values())for(const attachment of message.attachments||[])if(attachment.id===id)return attachment;
    return undefined;
  }
  private async receive(message:Item){
    switch(message.type){
      case 'ready':this.webviewReady=true;this.sentContacts=false;this.sentConversations=false;this.render();break;
      case 'selectPlatform':{
        if(this.selected?.platformId!==message.platformId){this.selected=undefined;this.historyGeneration++;this.messages.clear();this.nextBefore=undefined;this.render();}break;
      }
      case 'openModule':{
        const manifest=this.platforms.find(p=>p.id===message.platformId);
        const account=[...this.accounts.values()].find(a=>a.platformId===message.platformId);
        if(!account||!manifest?.modules?.includes(message.moduleId))throw new Error('平台未声明此模块');
        await this.modules.open({platformId:manifest.id,accountId:account.id,moduleId:message.moduleId,
          invoke:(operation,params={},commandId)=>this.request('modules.invoke',{accountId:account.id,module:message.moduleId,operation,params,commandId})});break;
      }
      case 'select':if(typeof message.id==='string')await this.select(message.id,typeof message.requestId==='string'?message.requestId:undefined);break;
      case 'history':await this.history();break;
      case 'send':{
        if(!this.selected||typeof message.text!=='string'||!message.text.trim())return;
        // 界面提交时固定目标，防止会话切换把草稿投递到另一个联系人。
        if(message.conversationId!==this.selected.id)throw new Error('会话已切换，请重新确认草稿');
        if(message.text.length>16000)throw new Error('文字超过 16000 字限制');
        const commandId=key();await this.enqueue({commandId,clientTag:message.clientTag,accountId:this.selected.accountId,conversationId:this.selected.id,kind:'text',text:message.text});
        this.view?.webview.postMessage({type:'sent',commandId,clientTag:message.clientTag,conversationId:message.conversationId,text:message.text});break;
      }
      case 'upload':{
        if(!this.selected)return;const selected=this.selected;
        const files=await vscode.window.showOpenDialog({canSelectMany:false,title:'选择图片、文件或音频文件'});if(!files)return;
        const stat=await fs.stat(files[0].fsPath);if(!stat.isFile()||stat.size<1||stat.size>32*1024*1024)throw new Error('请选择 1 字节至 32 MiB 的文件');
        const data=await fs.readFile(files[0].fsPath),name=path.basename(files[0].fsPath);
        // 平台限制在入队前提示，不能留下反复重试但无法发送的本地待发项。
        if(this.accounts.get(selected.accountId)?.capabilities?.['attachment.unicodeName']===false&&/[^\x00-\x7f]/.test(name))throw new Error('当前接入暂只支持英文、数字文件名，请重命名后再发送');
        const extension=path.extname(name).toLowerCase();const image=['.png','.jpg','.jpeg','.gif','.webp','.bmp'].includes(extension);
        await this.enqueue({commandId:key(),accountId:selected.accountId,conversationId:selected.id,kind:image?'image':'file',upload:{path:files[0].fsPath,name,bytes:data.length,sha256:createHash('sha256').update(data).digest('hex'),contentType:image?'image/'+extension.slice(1):'application/octet-stream',uploadKey:key()}});break;
      }
      case 'media':{
        const info=this.findAttachment(message.id);if(!info||!['ready','preview'].includes(info.state))return;
        const data=await this.attachment(info);if(data.length>12*1024*1024)throw new Error('预览限 12 MiB，请保存附件');
        await this.view?.webview.postMessage({type:'media',id:info.id,sha256:info.sha256,contentType:info.contentType,data:data.toString('base64')});break;
      }
      case 'save':{
        const info=this.findAttachment(message.id);if(!info||!['ready','preview'].includes(info.state))return;
        const destination=await vscode.window.showSaveDialog({defaultUri:vscode.Uri.file(path.join(process.env.USERPROFILE||'', 'Downloads',info.name))});if(!destination)return;
        await vscode.workspace.fs.writeFile(destination,await this.attachment(info));this.notify('附件已保存');break;
      }
      case 'retry':if(typeof message.id==='string')await this.request('attachments.retry',{messageId:message.id});break;
      case 'reconnect':this.reconnect();break;
      case 'pair':await this.importPairing();break;
      case 'diagnostics':await this.diagnostics();break;
      case 'cancelPending':{
        const answer=await vscode.window.showWarningMessage('移除本地尚未取得网关确认的待发项？已被网关接收的发送仍可能完成。','移除本地待发');
        if(answer){this.pending=[];await this.persist();this.render();}break;
      }
    }
  }
  async diagnostics(){
    try{const value=await this.request('diagnostics.get');this.output.appendLine(JSON.stringify(value,null,2));}catch(error){this.fail(error);}
    this.output.show(true);
  }
  status(){
    // 宿主可按不含正文的标识核对同步，避免只凭旧消息数量判断新消息已经到达。
    const mediaStates:Record<string,number>={};
    for(const message of this.messages.values())for(const attachment of message.attachments||[]){
      const key=attachment.kind+':'+attachment.state;mediaStates[key]=(mediaStates[key]||0)+1;
    }
    return {connection:this.state,detail:this.detail,accounts:[...this.accounts.values()].map(a=>({id:a.id,status:a.status})),contacts:this.contacts.size,conversations:this.conversations.size,messages:this.messages.size,messageIds:[...this.messages.keys()],mediaStates,cursor:this.cursor,bootstrapped:this.bootstrapped,viewReady:this.webviewReady,selectedId:this.selected?.id};
  }
  private notify(text:string){this.view?.webview.postMessage({type:'notice',text});}
  private fail(error:any){this.detail=error instanceof RemoteError?error.code:(error?.message||'操作失败');this.notify(error?.message||this.detail);this.output.appendLine(new Date().toISOString()+' '+this.detail);this.render();}
  private render(){
    if(!this.view)return;
    const outbox=[...this.pending.map(echo=>({echo,status:'queued'})),...this.tracked.values()].filter(item=>item.echo?.kind==='text').map(item=>({...item.echo,id:'local-'+item.echo!.commandId,timestamp:item.echo!.createdAt||Date.now(),direction:'outgoing',senderName:'我',status:item.status,attachments:[]}));
    this.view?.webview.postMessage({type:'state',connection:this.state,detail:this.detail,platforms:this.platforms,
      accounts:[...this.accounts.values()],...(!this.sentContacts?{contacts:[...this.contacts.values()]}:{}),...(!this.sentConversations?{conversations:[...this.conversations.values()]}:{}),
      messages:[...this.messages.values()].sort((a,b)=>a.timestamp-b.timestamp||a.id.localeCompare(b.id)),outbox,selected:this.selected,selectionRequest:this.selectionRequest,selectionRevision:this.historyGeneration,hasMore:this.nextBefore!==null,pending:this.pending.length});
    this.sentContacts=true;this.sentConversations=true;
  }
  resolveWebviewView(view:vscode.WebviewView){
    this.view=view;this.webviewReady=false;this.sentContacts=false;this.sentConversations=false;const webview=view.webview;webview.options={enableScripts:true,localResourceRoots:[vscode.Uri.joinPath(this.context.extensionUri,'media')]};
    const nonce=randomBytes(18).toString('base64');const script=webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri,'media','workbench.js'));
    const icons=webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri,'media','icons.js'));
    const messaging=webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri,'media','messaging.js'));
    const css=webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri,'media','workbench.css'));
    webview.html=`<!doctype html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; media-src data: blob:; style-src ${webview.cspSource}; script-src 'nonce-${nonce}';"><link rel="stylesheet" href="${css}"></head>
<body>
  <nav id="appRail" aria-label="应用切换">
    <div class="railBrand" title="ChatBridge" aria-label="ChatBridge"><span></span><span></span></div>
    <div id="appList"></div>
    <div class="railBottom"><button id="connectionDot" class="connectionButton" aria-label="连接状态"><span></span></button><button id="settings" class="appButton" title="设置" aria-label="设置" data-icon="settings" aria-pressed="false"></button></div>
  </nav>
  <main id="workspace">
    <div id="notice" role="status" hidden><span id="noticeText"></span><button id="noticeClose" class="iconButton" aria-label="关闭提示" data-icon="close"></button></div>
    <div id="pending"></div>
    <div id="appViewport"></div>
    <section id="welcome" class="welcome"><div class="welcomeMark" data-icon="chat"></div><h1>ChatBridge</h1><p>让消息，触手可及。</p><span class="subtle">连接后，你的应用会显示在左侧</span></section>
    <section id="settingsPage" hidden>
      <header class="settingsHeader"><button id="settingsBack" class="iconButton" aria-label="返回应用" data-icon="back"></button><h1>设置</h1></header>
      <div class="settingsBody"><span class="eyebrow">连接与设备</span><div class="connectionCard"><span id="status"></span><p id="accountStatus"></p><p id="connectionDetail" class="subtle"></p></div>
      <div class="settingsActions"><button id="reconnect" class="settingsRow" data-icon="refresh"><span><strong>重新连接</strong><small>恢复与聊天网关的连接</small></span></button><button id="pair" class="settingsRow" data-icon="key"><span><strong>设备配对</strong><small>导入自己的设备配对文件</small></span></button><button id="diagnostics" class="settingsRow" data-icon="pulse"><span><strong>连接诊断</strong><small>在输出面板查看运行状态</small></span></button></div>
      <div class="privacyNote" data-icon="shield"><span>消息在你的设备之间加密传输</span></div><div class="settingsBrand">ChatBridge <span>0.2.1</span></div></div>
    </section>
  </main>
  <template id="messagingTemplate">
    <div class="messagingApp" data-route="list">
      <section id="browser" class="listPage" aria-label="微信列表">
        <header class="listHeader"><div><h1 id="pageTitle">微信</h1><span id="appConnection" class="appConnection"></span></div><span class="headerMark" data-icon="wechat"></span></header>
        <div class="searchWrap"><span data-icon="search"></span><input id="search" type="search" placeholder="搜索会话" aria-label="搜索会话" autocomplete="off"><button id="clearSearch" class="iconButton" aria-label="清空搜索" data-icon="close" hidden></button></div>
        <div class="listSectionLabel"><span id="listTitle">会话</span><span id="listCount" class="countBadge">0</span></div>
        <div id="contacts" class="contactList" aria-label="会话或联系人列表"></div>
        <nav class="bottomTabs" role="tablist" aria-label="微信导航"><button id="recent" class="active" role="tab" aria-selected="true" data-icon="chat"><span>会话</span></button><button id="allContacts" role="tab" aria-selected="false" data-icon="contacts"><span>联系人</span></button></nav>
      </section>
      <section id="chatPage" class="chatPage" aria-label="聊天" hidden>
        <header class="chatHeader"><button id="back" class="iconButton" aria-label="返回会话列表" data-icon="back"></button><div class="chatTitle"><h1 id="conversationTitle"></h1><span id="conversationSubtitle"></span></div><span class="chatHeaderMark" data-icon="shield" title="加密连接"></span></header>
        <button id="history" class="historyButton" hidden>加载更早记录</button>
        <div id="messages" class="messages" role="log" aria-label="聊天消息" aria-live="polite" aria-relevant="additions text"></div>
        <footer class="composer"><div class="composerBox"><textarea id="draft" placeholder="发送消息…" aria-label="消息内容" rows="1" maxlength="16000"></textarea><div class="composerActions"><button id="upload" class="iconButton" title="发送图片、文件或音频" aria-label="发送图片、文件或音频" data-icon="attach"></button><span class="composeHint">Enter 发送 · Shift+Enter 换行</span><button id="send" class="sendButton" title="发送消息" aria-label="发送消息" data-icon="send"></button></div></div></footer>
      </section>
    </div>
  </template>
  <script nonce="${nonce}" src="${icons}"></script><script nonce="${nonce}" src="${messaging}"></script><script nonce="${nonce}" src="${script}"></script>
</body></html>`;
    view.webview.onDidReceiveMessage(message=>void this.receive(message).catch(error=>{if(message.type==='send')view.webview.postMessage({type:'sendFailed',clientTag:message.clientTag});this.fail(error);}));
    view.onDidDispose(()=>{if(this.view===view){this.view=undefined;this.webviewReady=false;}});this.render();
  }
  dispose(){clearInterval(this.timer);this.client?.stop();this.transport?.close();this.media.clear();}
}

export function activate(context:vscode.ExtensionContext){
  const output=vscode.window.createOutputChannel('ChatBridge'),modules=new Modules();const workbench=new Workbench(context,output,modules);
  context.subscriptions.push(output,workbench,vscode.window.registerWebviewViewProvider('chatbridge.chat',workbench,{webviewOptions:{retainContextWhenHidden:true}}),
    vscode.commands.registerCommand('chatbridge.open',()=>vscode.commands.executeCommand('workbench.view.extension.chatbridge')),
    vscode.commands.registerCommand('chatbridge.pair',()=>workbench.importPairing()),vscode.commands.registerCommand('chatbridge.reconnect',()=>workbench.reconnect()),
    vscode.commands.registerCommand('chatbridge.diagnostics',()=>workbench.diagnostics()));
  void workbench.initialize();
  return {getStatus:()=>workbench.status(),openConversation:(id:string)=>workbench.select(id),registerModule:(module:FrontendModule)=>modules.register(module)};
}
