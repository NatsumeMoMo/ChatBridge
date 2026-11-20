const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),{JSDOM}=require('jsdom');
function setup(initial={}){
 const source=fs.readFileSync('src/extension.ts','utf8');
 const html=source.match(/webview.html=`([\s\S]*?)`;/)[1].replace(/<script[^>]*>[\s\S]*?<\/script>/g,'');
 const dom=new JSDOM(html,{runScripts:'outside-only'}),window=dom.window,sent=[];
 window.acquireVsCodeApi=()=>({postMessage:value=>sent.push(value)});
 for(const file of ['icons','messaging','workbench'])window.eval(fs.readFileSync(`media/${file}.js`,'utf8'));
 const state={type:'state',connection:'online',platforms:[{id:'test',name:'Test',modules:['messaging']}],accounts:[{id:'a',platformId:'test',status:'online',capabilities:{'text.send':true,'file.send':true}}],contacts:[{id:'c',platformId:'test',accountId:'a',name:'<img src=x onerror=alert(1)>'}],messages:[],selected:{id:'c',platformId:'test',accountId:'a',name:'Chat'}};
 Object.assign(state,initial);
 const post=value=>window.dispatchEvent(new window.MessageEvent('message',{data:value}));post(state);
 return {dom,window,sent,state,post,$:id=>window.document.getElementById(id)};
}
test('webview asks for state after its listener is ready and treats chat text as plain text',()=>{
 const t=setup();try{
  assert.equal(t.sent[0].type,'ready');t.$('allContacts').click();assert.equal(t.$('contacts').querySelectorAll('img').length,0);
  t.post({...t.state,messages:[{id:'m',kind:'text',text:'<script>alert(1)</script>',timestamp:1,direction:'incoming'}]});
  assert.equal(t.$('messages').querySelectorAll('script').length,0);assert.match(t.$('messages').textContent,/<script>/);
 }finally{t.dom.window.close();}
});
test('send acknowledgement preserves another conversation draft and guards double clicks',()=>{
 const t=setup();try{
  t.$('draft').value='first';t.$('send').click();t.$('send').click();assert.equal(t.sent.filter(x=>x.type==='send').length,1);
  assert.equal(t.sent.find(x=>x.type==='send').conversationId,'c');
  t.post({...t.state,selected:{id:'other',platformId:'test',accountId:'a',name:'Other'}});t.$('draft').value='keep this';
  t.post({type:'sent',conversationId:'c',text:'first'});assert.equal(t.$('draft').value,'keep this');
  t.post({...t.state});assert.equal(t.$('draft').value,'');
 }finally{t.dom.window.close();}
});
test('send acknowledgement preserves text typed while persistence was in flight',()=>{
 const t=setup();try{t.$('draft').value='first';t.$('send').click();t.$('draft').value='second';t.post({type:'sent',conversationId:'c',text:'first'});assert.equal(t.$('draft').value,'second');}finally{t.dom.window.close();}
});

test('text appears immediately before persistence or network response and merges by command id',()=>{
 const t=setup();try{
  t.$('draft').value='instant';const started=performance.now();t.$('send').click();
  assert.match(t.$('messages').textContent,/instant/);assert.match(t.$('messages').textContent,/正在保存待发记录/);
  const localMs=performance.now()-started;assert.ok(localMs<500,'local echo must not wait for a network round trip');
  const request=t.sent.find(item=>item.type==='send');
  const echo={id:'local-command',commandId:'command',clientTag:request.clientTag,conversationId:'c',kind:'text',text:'instant',timestamp:1,direction:'outgoing',status:'submitted'};
  t.post({...t.state,outbox:[echo,echo]});assert.equal(t.$('messages').querySelectorAll('article').length,1);assert.match(t.$('messages').textContent,/等待平台回执/);
  const native={...echo,id:'native',status:'serverAccepted'};
  t.post({...t.state,outbox:[echo],messages:[native]});assert.equal(t.$('messages').querySelectorAll('article').length,1);assert.match(t.$('messages').textContent,/服务器已接收/);
  const second={...echo,id:'local-second',clientTag:'another',commandId:'second'};
  t.post({...t.state,outbox:[echo,second],messages:[native]});assert.equal(t.$('messages').querySelectorAll('article').length,2);
 }finally{t.dom.window.close();}
});

test('failed local persistence never claims delivery and keeps the draft',()=>{
 const t=setup();try{
  t.$('draft').value='keep';t.$('send').click();const request=t.sent.find(item=>item.type==='send');
  t.post({type:'sendFailed',clientTag:request.clientTag});assert.equal(t.$('draft').value,'keep');
  assert.match(t.$('messages').textContent,/未保存/);assert.doesNotMatch(t.$('messages').textContent,/服务器已接收/);
 }finally{t.dom.window.close();}
});

test('message-only updates keep the contact catalog and selection available',()=>{
 const t=setup();try{
  t.$('allContacts').click();const contact=t.$('contacts').firstChild;
  t.post({type:'state',messages:[{id:'m',kind:'text',text:'delta',timestamp:1,direction:'incoming'}]});
  assert.equal(t.$('contacts').firstChild,contact);assert.match(t.$('messages').textContent,/delta/);assert.equal(t.$('send').disabled,false);
 }finally{t.dom.window.close();}
});

test('defaults to a full-page conversation list and returns to the entry tab with its search and scroll',()=>{
 const item={id:'c',platformId:'test',accountId:'a',name:'Chat'};
 const t=setup({selected:undefined,contacts:[item],conversations:[item]});try{
  assert.equal(t.$('browser').hidden,false);assert.equal(t.$('chatPage').hidden,true);
  assert.equal(t.$('recent').getAttribute('aria-selected'),'true');
  t.$('allContacts').click();t.$('search').value='Chat';t.$('search').dispatchEvent(new t.window.Event('input'));
  t.$('contacts').scrollTop=123;t.$('contacts').firstChild.click();
  const request=t.sent.findLast(x=>x.type==='select');
  assert.equal(t.$('browser').hidden,true);assert.equal(t.$('chatPage').hidden,false);assert.equal(t.$('send').disabled,true);
  t.post({type:'state',selected:item,selectionRequest:request.requestId});t.$('draft').value='draft from contacts';
  t.$('back').click();assert.equal(t.$('browser').hidden,false);assert.equal(t.$('chatPage').hidden,true);
  assert.equal(t.$('allContacts').getAttribute('aria-selected'),'true');assert.equal(t.$('search').value,'Chat');assert.equal(t.$('contacts').scrollTop,123);
  t.$('recent').click();assert.equal(t.$('search').value,'');t.$('allContacts').click();assert.equal(t.$('search').value,'Chat');
  t.$('contacts').firstChild.click();assert.equal(t.$('draft').value,'draft from contacts');
  t.$('back').click();t.$('recent').click();t.$('contacts').firstChild.click();t.$('back').click();
  assert.equal(t.$('recent').getAttribute('aria-selected'),'true');
 }finally{t.dom.window.close();}
});

test('returning before a selection response arrives stays on the list and late replies cannot retarget a newer conversation',()=>{
 const items=['a','b'].map(id=>({id,platformId:'test',accountId:'a',name:id}));
 const t=setup({selected:undefined,contacts:items,conversations:items});try{
  t.$('contacts').firstChild.click();const first=t.sent.findLast(x=>x.type==='select');t.$('back').click();
  t.post({type:'state',selected:items[0],selectionRequest:first.requestId,messages:[]});assert.equal(t.$('chatPage').hidden,true);
  t.$('contacts').children[1].click();const second=t.sent.findLast(x=>x.type==='select');
  t.post({type:'state',selected:items[0],selectionRequest:first.requestId,messages:[{id:'wrong',kind:'text',text:'wrong conversation',timestamp:1,direction:'incoming'}]});
  assert.equal(t.$('send').disabled,true);assert.doesNotMatch(t.$('messages').textContent,/wrong conversation/);
  t.post({type:'state',selected:items[1],selectionRequest:second.requestId,messages:[]});assert.equal(t.$('send').disabled,false);assert.equal(t.$('conversationTitle').textContent,'b');
 }finally{t.dom.window.close();}
});

test('host can explicitly reopen the same conversation after the page returned to the list',()=>{
 const t=setup();try{
  t.$('back').click();assert.equal(t.$('chatPage').hidden,true);
  t.post({type:'state',selected:t.state.selected,selectionRequest:null,selectionRevision:2});assert.equal(t.$('chatPage').hidden,false);
 }finally{t.dom.window.close();}
});

test('application shell supports an independent page and restores the messaging draft after switching applications',()=>{
 const t=setup();try{
  t.$('draft').value='keep across apps';let updates=0;
  t.window.ChatBridgeApps.register('feed',()=>{const node=t.window.document.createElement('div');node.id='independentFeed';node.textContent='Independent feed layout';return {element:node,update(){updates++;}};});
  const platforms=[...t.state.platforms,{id:'feed',name:'Feed',modules:['feed']}];
  t.post({type:'state',platforms});t.window.document.querySelector('[data-app-id="feed"]').click();
  assert.equal(t.$('appViewport').textContent,'Independent feed layout');assert.equal(t.$('contacts'),null);assert.ok(updates>0);
  assert.equal(t.sent.findLast(x=>x.type==='selectPlatform').platformId,'feed');
  t.window.document.querySelector('[data-app-id="test"]').click();assert.equal(t.$('draft').value,'keep across apps');
  assert.equal(t.$('chatPage').hidden,false);assert.equal(t.sent.findLast(x=>x.type==='select').id,'c');
 }finally{t.dom.window.close();}
});

test('settings preserve the open conversation and diagnostics remain accessible',()=>{
 const t=setup();try{
  const rail=t.$('appRail');t.$('draft').value='keep settings draft';t.$('settings').click();
  assert.equal(t.$('settingsPage').hidden,false);assert.equal(t.$('appViewport').hidden,true);
  t.$('diagnostics').click();assert.equal(t.sent.findLast(x=>x.type==='diagnostics').type,'diagnostics');
  t.$('settingsBack').click();assert.equal(t.$('settingsPage').hidden,true);assert.equal(t.$('draft').value,'keep settings draft');assert.equal(t.$('appRail'),rail);
 }finally{t.dom.window.close();}
});

test('a failing application cannot break switching back to chat or opening settings',()=>{
 const t=setup();try{
  t.$('draft').value='survives another app error';
  t.window.ChatBridgeApps.register('broken',()=>({element:t.window.document.createElement('div'),update(){throw Error('demo failure');}}));
  t.post({type:'state',platforms:[...t.state.platforms,{id:'broken',name:'Broken',modules:['feed']}]});
  t.window.document.querySelector('[data-app-id="broken"]').click();assert.match(t.$('appViewport').textContent,/应用界面暂时不可用/);
  t.$('settings').click();assert.equal(t.$('settingsPage').hidden,false);t.$('settingsBack').click();
  t.window.document.querySelector('[data-app-id="test"]').click();assert.equal(t.$('draft').value,'survives another app error');
 }finally{t.dom.window.close();}
});

test('loaded voice controls stay in place through unrelated message updates and unsafe media types are ignored',()=>{
 const t=setup();try{
  const voice={id:'v',kind:'voice',direction:'incoming',timestamp:1,attachments:[{id:'att',kind:'voice',name:'voice.wav',state:'ready',sha256:'abc'}]};
  t.post({type:'state',messages:[voice]});const audio=t.$('messages').querySelector('audio');
  t.post({type:'media',id:'att',sha256:'abc',contentType:'text/html',data:'PHNjcmlwdD4='});assert.equal(audio.getAttribute('src'),null);
  t.post({type:'media',id:'att',sha256:'abc',contentType:'audio/wav',data:'UklGRg=='});assert.equal(audio.hidden,false);
  let removed=false;const observer=new t.window.MutationObserver(records=>{if(records.some(r=>[...r.removedNodes].some(n=>n===audio||n.contains?.(audio))))removed=true;});observer.observe(t.$('messages'),{childList:true,subtree:true});
  t.post({type:'state',messages:[voice,{id:'new',kind:'text',text:'new message',timestamp:2,direction:'incoming'}]});
  t.window.document.querySelector('[data-app-id="test"]').click();
  for(const record of observer.takeRecords())if([...record.removedNodes].some(n=>n===audio||n.contains?.(audio)))removed=true;
  assert.equal(t.$('messages').querySelector('audio'),audio);assert.equal(removed,false);observer.disconnect();
 }finally{t.dom.window.close();}
});
