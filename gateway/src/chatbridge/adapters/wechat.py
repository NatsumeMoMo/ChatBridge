import asyncio,datetime,hashlib,json,logging,re,time,uuid
from pathlib import Path
import zstandard
from defusedxml import ElementTree as ET
from ..errors import BridgeError
LOG=logging.getLogger('chatbridge')

def sql_string(value):return "'"+str(value).replace("'","''")+"'"
def decode(value):
    if not value:return ''
    if not re.fullmatch(r'(?:[0-9a-fA-F]{2})+',value):return value
    raw=bytes.fromhex(value)
    if raw.startswith(b'\x28\xb5\x2f\xfd'):raw=zstandard.ZstdDecompressor().decompress(raw,max_output_size=1024*1024)
    return raw.decode('utf-8',errors='replace')

class Hook:
    def __init__(self,queue):self.queue=Path(queue);self.lock=asyncio.Lock()
    def _call(self,data,timeout):
        identifier=uuid.uuid4().hex;request=self.queue/('req-'+identifier+'.json');temporary=request.with_suffix('.tmp')
        response=self.queue/('res-'+identifier+'.json')
        temporary.write_text(json.dumps({'id':identifier,**data},ensure_ascii=False),encoding='utf-8');temporary.replace(request)
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            if response.exists():
                try:result=json.loads(response.read_text(encoding='utf-8-sig'))
                except (ValueError,OSError):time.sleep(.1);continue
                response.unlink();return result
            time.sleep(.01)
        raise BridgeError('HOOK_TIMEOUT','微信接口响应超时；发送结果需查账',True)
    async def call(self,data,timeout=12):
        async with self.lock:return await asyncio.to_thread(self._call,data,timeout)
    async def query(self,database,sql):
        result=await self.call({'op':'queryDB','optDbName':database,'SQL':sql})
        data=result.get('data',{})
        if not result.get('ok') or data.get('status')!=0:
            table=re.search(r'\bFROM\s+([a-zA-Z0-9_]+)',sql,re.IGNORECASE)
            LOG.warning('wechat_query_failed database=%s table=%s status=%s reason=%s',database,table[1] if table else 'schema',data.get('status'),str(data.get('desc',''))[:160])
            raise BridgeError('WECHAT_DATABASE_UNAVAILABLE','微信数据库尚未就绪',True)
        return data.get('data',[])

class WeChat:
    manifest={'id':'wechat','name':'微信','adapterVersion':'0.2.1','modules':['messaging'],
              'capabilities':{'contacts':True,'groups':True,'text.send':True,'image.send':True,'file.send':True,
                              'voice.receive':True,'voice.send':False,'audioFile.send':True,'attachment.unicodeName':False,
                              'image.original':False,'media.activeDownload':False}}
    account_id='wechat:primary'
    def __init__(self,store,config):
        self.account_id=config.get('accountId',self.account_id)
        self.store=store;self.config=config;self.hook=Hook(config['hookQueue']);self.contacts={};self.databases=[]
        self.checkpoint_key='adapter.'+self.account_id+'.checkpoints';integration_key='account.'+self.account_id+'.integratedAt'
        self.started=store.meta(integration_key)
        if self.started is None:
            # 迁移现有账号时保留接入边界，新加账号则从自己的接入时间开始。
            self.started=store.meta('integratedAt') if store.get('account',self.account_id) else int(time.time())
            store.set_meta(integration_key,self.started)
        self.refreshed=0;self.checkpoints=store.meta(self.checkpoint_key,store.meta('wechat.checkpoints',{}) if self.account_id=='wechat:primary' else {})
        self.pending_media={};self.media_due={};self.message=asyncio.Event()
        self.known_tables=set();self.capture_locks={};self.captured_at={};self.session_seen={};self.session_attempted={}
        self.table_catalog={};self.table_catalog_at={};self.contact_offset=None;self.contact_page_at=0
        self.saved_checkpoints=dict(self.checkpoints);self.poll_stats={}
    def conversation_id(self,native):return self.store.identity(self.account_id,'conversation',native)
    def put_contacts(self,rows):
        for row in rows:
            native=row['username']
            if not native:continue
            self.contacts[native]=row;group=native.endswith('@chatroom')
            if native.startswith(('gh_','@')) and not group:continue
            self.store.put('contact',{'id':self.conversation_id(native),'accountId':self.account_id,'platformId':'wechat',
                           'name':row.get('remark') or row.get('nick_name') or native,'kind':'group' if group else 'direct','_native':native},sort=0)

    async def contact_page(self,offset):
        rows=await self.hook.query('contact.db','SELECT username,remark,nick_name,local_type,delete_flag FROM contact WHERE delete_flag=0 ORDER BY id LIMIT 200 OFFSET '+str(offset))
        self.put_contacts(rows);self.contact_page_at=time.monotonic()
        return offset+len(rows) if len(rows)==200 and offset+len(rows)<20000 else None

    async def refresh(self,incremental=False):
        status=await self.hook.call({'op':'status'})
        logged=status.get('data',{}).get('IsLogin')==1
        if not logged:self.store.put('account',{'id':self.account_id,'platformId':'wechat','name':'个人微信',
                                 'status':'loginRequired','capabilities':self.manifest['capabilities']})
        if not logged:raise BridgeError('WECHAT_LOGIN_REQUIRED','微信需要登录',True)
        current=self.store.get('account',self.account_id)
        if current is None or current.get('status')=='loginRequired':
            self.store.put('account',{'id':self.account_id,'platformId':'wechat','name':'个人微信','status':'degraded',
                                     'lastErrorCode':'WECHAT_DATABASE_NOT_READY','capabilities':self.manifest['capabilities']})
        databases=await self.hook.call({'op':'dbs'})
        self.databases=sorted({row['dbName'] for row in databases.get('data',{}).get('dbs',[]) if re.fullmatch(r'message_\d+\.db',row.get('dbName',''))})
        self.contact_offset=await self.contact_page(0)
        while self.contact_offset is not None and not incremental:self.contact_offset=await self.contact_page(self.contact_offset)
        if not self.store.get('contact',self.conversation_id('filehelper')):
            self.store.put('contact',{'id':self.conversation_id('filehelper'),'accountId':self.account_id,'platformId':'wechat','name':'文件传输助手','kind':'direct','_native':'filehelper'})
        self.refreshed=time.monotonic()

    async def poll(self):
        started=time.monotonic()
        if not self.refreshed:await self.refresh()
        rows=await self.hook.query('session.db','SELECT username,last_timestamp,last_msg_locald_id,unread_count FROM SessionTable WHERE is_hidden=0 ORDER BY sort_timestamp DESC LIMIT 500')
        missing=[row['username'] for row in rows if not self.store.get('contact',self.conversation_id(row['username'])) and not row['username'].startswith(('gh_','@'))]
        # 新好友先做定向补录，不必等待下一次完整通讯录刷新才能接收消息。
        for offset in range(0,len(missing),100):
            names=','.join(sql_string(name) for name in missing[offset:offset+100])
            self.put_contacts(await self.hook.query('contact.db','SELECT username,remark,nick_name,local_type,delete_flag FROM contact WHERE delete_flag=0 AND username IN ('+names+')'))
        urgent=[];background=[]
        for session in rows:
            native=session['username'];cid=self.conversation_id(native)
            contact=self.store.get('contact',cid)
            if not contact:continue
            current=self.store.get('conversation',cid) or {**contact,'lastMessageAt':0,'preview':''}
            # 接入前的会话可选择，但旧正文不会作为接入后的新历史导入。
            conversation={**current,'lastMessageAt':int(session.get('last_timestamp') or 0)*1000,'unread':int(session.get('unread_count') or 0)}
            self.store.put('conversation',conversation,sort=conversation['lastMessageAt'])
            if int(session.get('last_timestamp') or 0)<self.started:continue
            signature=(session.get('last_timestamp'),session.get('last_msg_locald_id'))
            previous=self.session_seen.get(native)
            # 新变化优先于历史回查；置顶会话的排序不能让刚收到的好友消息排在维护队列后面。
            if previous and previous[0]==signature and time.monotonic()-previous[1]<30:continue
            item=(native,cid,signature)
            if previous and previous[0]!=signature or not previous and int(session.get('last_timestamp') or 0)>=time.time()-30:urgent.append(item)
            else:background.append(item)
        urgent.sort(key=lambda item:int(item[2][0] or 0),reverse=True)
        background.sort(key=lambda item:self.session_attempted.get(item[0],0))
        budget=max(0,int(self.config.get('backgroundCaptureLimit',1)))
        for native,cid,signature in urgent+background[:budget]:
            self.session_attempted[native]=time.monotonic()
            captured=await self.capture(native,cid,force=True)
            if captured is not False:self.session_seen[native]=(signature,time.monotonic())
        if self.checkpoints!=self.saved_checkpoints:
            self.store.set_meta(self.checkpoint_key,self.checkpoints);self.saved_checkpoints=dict(self.checkpoints)
        # 定期目录刷新拆成小页并放在消息之后，避免几千联系人连续占住原生队列。
        if time.monotonic()-self.refreshed>self.config.get('contactRefreshSeconds',300):await self.refresh(incremental=True)
        elif self.contact_offset is not None and time.monotonic()-self.contact_page_at>=self.config.get('contactPageSeconds',2):
            self.contact_offset=await self.contact_page(self.contact_offset)
        self.poll_stats={'durationMs':round((time.monotonic()-started)*1000,1),'sessions':len(rows),'urgent':len(urgent),
                         'backgroundCaptured':min(budget,len(background)),'backgroundRemaining':max(0,len(background)-budget)}
        # 登录标记早于数据库可读，整轮采集成功后才允许发送任务消费队列。
        self.store.put('account',{'id':self.account_id,'platformId':'wechat','name':'个人微信','status':'online','capabilities':self.manifest['capabilities']})

    async def capture(self,native,cid,force=False):
        async with self.capture_locks.setdefault(native,asyncio.Lock()):
            if not force and time.monotonic()-self.captured_at.get(native,0)<.15:return
            result=await self._capture(native,cid);self.captured_at[native]=time.monotonic();return result

    async def has_table(self,database,table,discover=False):
        now=time.monotonic();age=now-self.table_catalog_at.get(database,0)
        missing=table not in self.table_catalog.get(database,set())
        if database not in self.table_catalog or age>=self.config.get('tableRefreshSeconds',30) or discover and missing and age>=1:
            # 一次读取整个分库目录，空分库不再对每个联系人重复查表。
            rows=await self.hook.query(database,"SELECT name FROM sqlite_master WHERE type='table' AND name GLOB 'Msg_*'")
            self.table_catalog[database]={row['name'] for row in rows};self.table_catalog_at[database]=time.monotonic()
            self.known_tables.update((database,name) for name in self.table_catalog[database])
        return table in self.table_catalog[database]

    async def _capture(self,native,cid):
        table='Msg_'+hashlib.md5(native.encode()).hexdigest()
        known=any((database,table) in self.known_tables for database in self.databases);found=False
        for database in self.databases:
            if not await self.has_table(database,table,discover=not known):continue
            found=True
            key=database+':'+table;cursor=self.checkpoints.get(key,0)
            while True:
                minimum=max(1,cursor-20)
                rows=await self.hook.query(database,'SELECT m.local_id,m.local_type,m.server_id,m.create_time,m.real_sender_id,m.status,m.upload_status,m.download_status,m.message_content,n.user_name AS sender_name FROM '+table+' AS m LEFT JOIN Name2Id AS n ON n.rowid=m.real_sender_id WHERE m.create_time>='+str(self.started)+' AND m.local_id>='+str(minimum)+' ORDER BY m.local_id LIMIT 200')
                if not rows:break
                senders={}
                for row in rows:senders[str(row['real_sender_id'])]=row.get('sender_name','')
                for row in rows:
                    value=self.normalize(database,native,cid,row,senders.get(str(row['real_sender_id']),''))
                    self.store.put('message',value,cid,value['timestamp'])
                    cursor=max(cursor,int(row['local_id']));self.checkpoints[key]=cursor
                    conversation=self.store.get('conversation',cid)
                    if conversation and value['timestamp']>=conversation.get('lastMessageAt',0):
                        self.store.put('conversation',{**conversation,'lastMessageAt':value['timestamp'],'preview':value['text'][:80]},sort=value['timestamp'])
                if len(rows)<200:break
                # 最近状态需要回查，但翻页时必须向前推进，避免大批量消息被尾部回查困住。
                more=await self.hook.query(database,'SELECT MAX(local_id) AS maximum FROM '+table+' WHERE create_time>='+str(self.started))
                if not more or cursor>=int(more[0].get('maximum') or 0):break
        self.message.set()
        return found

    def normalize(self,database,native,cid,row,sender):
        local_id=int(row['local_id']);kind=int(row['local_type'])&0xffffffff
        identifier=self.store.identity(self.account_id,database,native,local_id)
        old=self.store.get('message',identifier)
        content=decode(row.get('message_content',''));group=native.endswith('@chatroom')
        outgoing=sender!=native if not group else sender==self.config.get('ownId')
        sender_contact=self.contacts.get(sender,{})
        value={'id':identifier,'accountId':self.account_id,'conversationId':cid,'platformId':'wechat',
               'direction':'outgoing' if outgoing else 'incoming','senderName':sender_contact.get('remark') or sender_contact.get('nick_name') or ('我' if outgoing else sender),
               'timestamp':int(row['create_time'])*1000,'kind':'text','text':content,'attachments':[],
               'status':'serverAccepted' if str(row.get('server_id','0'))!='0' else 'pending',
               '_source':{'database':database,'native':native,'row':row,'sender':sender}}
        if group and ':\n' in content and not content.lstrip().startswith('<'):
            prefix,body=content.split(':\n',1)
            if prefix==sender:value['text']=body
        try:
            if kind in (3,34,43,49,10000) and '<' in content:
                xml=ET.fromstring(content[content.index('<'):]);value['_source']['xml']=content
                if kind==3:
                    item=xml.find('.//img');value.update(kind='image',text='[图片]')
                    if item is not None:value['_source']['image']=dict(item.attrib)
                elif kind==34:value.update(kind='voice',text='[语音]')
                elif kind==49:
                    app=xml.find('.//appmsg')
                    if app is not None:
                        app_type=app.findtext('type');title=app.findtext('title') or '附件'
                        value.update(kind='file' if app_type=='6' else 'rich',text=title)
                        value['_source']['file']={'title':title,'bytes':int(app.findtext('appattach/totallen') or 0),
                                                   'md5':app.findtext('md5') or app.findtext('appattach/md5') or ''}
                elif kind==10000 and ('revokemsg' in content or '撤回' in content):value.update(kind='system',text='[撤回通知，原记录保留在历史中]')
        except Exception:value.update(kind='unsupported',text='[暂不支持解析的消息]')
        if kind==34:value.update(kind='voice',text='[语音]')
        if old and old.get('commandId'):value['commandId']=old['commandId']
        if value['kind'] in ('image','voice','file'):
            aid=self.store.identity('attachment',identifier)
            previous=old.get('attachments',[]) if old else []
            value['attachments']=previous or [{'id':aid,'state':'pending','kind':value['kind'],'name':value['text']}]
        return value

    async def send(self,command):
        request=command['request'];conversation=self.store.get('contact',request['conversationId']) or self.store.get('conversation',request['conversationId'])
        if not conversation:raise BridgeError('CONVERSATION_NOT_FOUND')
        native=conversation['_native'];table='Msg_'+hashlib.md5(native.encode()).hexdigest();baselines={}
        for database in self.databases:
            exists=(database,table) in self.known_tables or await self.hook.query(database,"SELECT name FROM sqlite_master WHERE name="+sql_string(table))
            if exists:
                self.known_tables.add((database,table))
                rows=await self.hook.query(database,'SELECT COALESCE(MAX(local_id),0) AS maximum FROM '+table)
                baselines[database]=int(rows[0]['maximum'])
        command['nativeBaseline']=baselines;command['submittedAt']=int(time.time())
        self.store.command_update(command['id'],'dispatching',command)
        if request['kind']=='text':
            operation={'op':'sendText','wxidorgid':native,'msg':request['text'],'atlist':[]}
        else:
            attachment=self.store.get('attachment',request['attachmentId'])
            if not attachment or attachment.get('ownerPeer')!=command.get('peer'):raise BridgeError('ATTACHMENT_NOT_FOUND')
            # 命令目录隔离重名文件，同时保留微信接收端看到的原始文件名。
            data=self.store.read_blob(attachment['id']);directory=Path(self.config['staging'])/command['id'];directory.mkdir(exist_ok=True)
            path=directory/attachment['name']
            path.write_bytes(data);command['stagingPath']=str(path)
            self.store.command_update(command['id'],'dispatching',command)
            operation={'op':'sendImage','wxid':native,'imgPath':str(path)}
        response=await self.hook.call(operation,18)
        command['localApiAccepted']=bool(response.get('ok'))
        command['_nativeResult']=response
        self.store.command_update(command['id'],'dispatching',command)
        if not command['localApiAccepted']:raise BridgeError('SEND_UNCERTAIN','微信未明确确认本地提交，请核对结果')
        return command

    async def reconcile(self,command):
        request=command['request'];contact=self.store.get('contact',request['conversationId']) or self.store.get('conversation',request['conversationId'])
        if not contact:return None
        # 旧命令的迟到回执仍查持久记录，原生采集由公共轮询负责，不能反复抢占新发送。
        if int(time.time())-command.get('submittedAt',command['createdAt'])<180:await self.capture(contact['_native'],contact['id'])
        candidates=[]
        for message in self.store.list('message',contact['id'],limit=100,reverse=True):
            source=message['_source'];row=source['row']
            if int(row['local_id'])<=command.get('nativeBaseline',{}).get(source['database'],0):continue
            if message['direction']!='outgoing' or int(row['create_time'])<command.get('submittedAt',command['createdAt'])-2:continue
            # 同文案可能稍后再次人工发送，不能让旧的不确定请求认领新消息。
            if int(row['create_time'])>command.get('submittedAt',command['createdAt'])+120:continue
            if not self.store.receipt_available(message['id'],command['id']):continue
            if message['status']!='serverAccepted':continue
            if request['kind']=='text':matches=message['kind']=='text' and message['text']==request['text']
            else:
                attachment=self.store.get('attachment',request['attachmentId'])
                matches=bool(attachment) and (source.get('image',{}).get('md5')==attachment.get('md5') or
                    (source.get('file',{}).get('md5')==attachment.get('md5') and source.get('file',{}).get('title') in
                     (attachment['name'],Path(command.get('stagingPath','')).name)))
            if matches:candidates.append(message)
        if len(candidates)==1:
            matched=candidates[0]
            if request['kind']!='text':
                original=self.store.get('attachment',request['attachmentId'])
                aid=self.store.identity('sent-attachment',matched['id'])
                saved=self.store.save_blob(aid,self.store.read_blob(original['id']))
                attachment={**saved,'state':'ready','kind':request['kind'],'name':original['name'],'contentType':original['contentType'],'messageId':matched['id']}
                self.store.put('attachment',attachment,matched['id'])
                self.store.put('message',{**matched,'attachments':[attachment]},matched['conversationId'],matched['timestamp'])
            if command.get('stagingPath'):
                path=Path(command['stagingPath']);path.unlink(missing_ok=True)
                if path.parent.name==command['id']:path.parent.rmdir()
            return {**command,'messageId':matched['id']}
        return None

    async def media(self,message):
        from ..media import resolve
        return await resolve(self,message)
