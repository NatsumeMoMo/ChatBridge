import asyncio,base64,hashlib,json,logging,os,re,ssl,time,uuid
from collections import OrderedDict
from pathlib import Path
from . import protocol
from .errors import BridgeError,require
from .media import safe_name
from .security import load_tls_chain

LOG=logging.getLogger('chatbridge')

def public(value):
    if isinstance(value,dict):return {k:public(v) for k,v in value.items() if not k.startswith('_') and k not in ('ownerPeer','originalBlobId')}
    if isinstance(value,list):return [public(v) for v in value]
    if isinstance(value,str):return value[:32768]
    return value

def page(items,budget=110000):
    result=[];size=0
    for item in items:
        data=public(item);length=len(json.dumps(data,ensure_ascii=False).encode())
        if result and size+length>budget:break
        result.append(data);size+=length
    return result

class Gateway:
    def __init__(self,store,adapters,config,secrets):
        self.store=store;self.adapters={a.account_id:a for a in adapters};self.config=config;self.secrets=secrets
        self.tasks=[];self.stopping=asyncio.Event();self.changed=asyncio.Event();self.active_peers={};self.errors={};self.reconcile_due={}
        self.ready_accounts=set()
        self.work_events={account:asyncio.Event() for account in self.adapters}
        self.boot_id=uuid.uuid4().hex;self.store.on_change=self.notify_change
        self.blob_cache=OrderedDict();self.blob_cache_bytes=0
    def notify_change(self):
        # 换一个事件对象唤醒全部订阅者，不能让先醒的设备清掉其它设备的通知。
        previous=self.changed;self.changed=asyncio.Event();previous.set()
    def read_blob(self,info):
        key=(info['id'],info['sha256']);now=time.monotonic()
        for old,(at,data) in list(self.blob_cache.items()):
            if now-at>300:self.blob_cache_bytes-=len(data);del self.blob_cache[old]
        if key in self.blob_cache:
            _,data=self.blob_cache.pop(key);self.blob_cache[key]=(now,data);return data
        # 分块请求不能每次都重解密整份文件；缓存只留端点内存且按内容哈希失效。
        data=self.store.read_blob(info['id'])
        while self.blob_cache and self.blob_cache_bytes+len(data)>64*1024*1024:
            _,(_,old)=self.blob_cache.popitem(last=False);self.blob_cache_bytes-=len(old)
        if len(data)<=64*1024*1024:self.blob_cache[key]=(now,data);self.blob_cache_bytes+=len(data)
        return data
    def authorized(self,fingerprint):
        # 每次心跳重新读取名单，使撤销设备不必重启网关。
        devices=json.loads(Path(self.config['devicesFile']).read_text(encoding='utf-8-sig'))
        return fingerprint in devices.get('allowed',[])
    def tls(self):
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version=context.maximum_version=ssl.TLSVersion.TLSv1_3
        context.verify_mode=ssl.CERT_REQUIRED
        context.load_verify_locations(cadata=self.secrets['caPem'])
        load_tls_chain(context,self.secrets['serverCertPem'],self.secrets['serverKeyPem'],self.config['staging'])
        return context
    async def start(self):
        self.store.set_meta('gateway.runtime',{'bootId':self.boot_id,'pid':os.getpid(),'startedAt':time.time()})
        # 上次进程留下的在线状态不能证明本次适配器已就绪。
        for account in self.adapters:
            current=self.store.get('account',account)
            if current and current.get('status')!='loginRequired':
                self.store.put('account',{**current,'status':'degraded','lastErrorCode':'ADAPTER_INITIALIZING'})
        server=await asyncio.start_server(self.connection,self.config.get('bind','127.0.0.1'),self.config.get('port',19443),ssl=self.tls(),ssl_handshake_timeout=10,limit=protocol.MAX_PADDED+16)
        # 一个平台的登录或接口变慢不阻塞其它平台的收发循环。
        self.tasks=[task for account,adapter in self.adapters.items() for task in
                    (asyncio.create_task(self.worker(account,adapter)),asyncio.create_task(self.poller(account,adapter)))]+[asyncio.create_task(self.media_worker())]
        return server
    async def close(self):
        self.stopping.set()
        for task in self.tasks:task.cancel()
        await asyncio.gather(*self.tasks,return_exceptions=True)
        self.store.on_change=None
        self.blob_cache.clear();self.blob_cache_bytes=0
    async def connection(self,reader,writer):
        ssl_object=writer.get_extra_info('ssl_object');certificate=ssl_object.getpeercert(binary_form=True)
        fingerprint=hashlib.sha256(certificate).hexdigest();hello=False;admitted=False;subscription=None
        write_lock=asyncio.Lock()
        async def write(value):
            async with write_lock:
                if not self.authorized(fingerprint):writer.close();raise ConnectionError('DEVICE_REVOKED')
                await protocol.write(writer,value)
        async def push(after):
            try:
                while not self.stopping.is_set():
                    if not self.authorized(fingerprint):break
                    changed=self.changed;events=page(self.store.events(after,50))
                    if events:
                        after=events[-1]['cursor']
                        await write({'type':'events','events':events,'cursor':after,'latestCursor':self.store.latest()})
                        await asyncio.sleep(0)
                    else:
                        try:await asyncio.wait_for(changed.wait(),20)
                        except asyncio.TimeoutError:pass
            except (ConnectionError,asyncio.TimeoutError):pass
            except Exception:LOG.error('event_push_failed')
            if not self.stopping.is_set():writer.close()
        try:
            if not self.authorized(fingerprint):return
            if self.active_peers.get(fingerprint,0)>=3:return
            self.active_peers[fingerprint]=self.active_peers.get(fingerprint,0)+1
            admitted=True
            while not self.stopping.is_set():
                envelope=await asyncio.wait_for(protocol.read(reader),65)
                if not self.authorized(fingerprint):break
                require(envelope.get('type')=='request')
                identifier=envelope.get('id');operation=envelope.get('op');args=envelope.get('args',{})
                require(isinstance(identifier,str) and 1<=len(identifier)<=80 and isinstance(operation,str) and isinstance(args,dict))
                try:
                    if not hello and operation!='hello':raise BridgeError('HELLO_REQUIRED')
                    result=await self.route(fingerprint,operation,args)
                    if operation=='hello':hello=True
                    response={'type':'response','id':identifier,'ok':True,'result':result}
                except BridgeError as error:
                    response={'type':'response','id':identifier,'ok':False,'error':{'code':error.code,'message':error.message,'retryable':error.retryable}}
                except (TypeError,ValueError):
                    response={'type':'response','id':identifier,'ok':False,'error':{'code':'INVALID_ARGUMENT','message':'请求参数无效','retryable':False}}
                except Exception:
                    LOG.error('request_failed op=%s id=%s',operation,identifier)
                    response={'type':'response','id':identifier,'ok':False,'error':{'code':'INTERNAL_ERROR','message':'请求未完成，请查阅诊断状态','retryable':False}}
                await write(response)
                if response.get('ok') and operation=='sync.subscribe':
                    if subscription:
                        subscription.cancel();await asyncio.gather(subscription,return_exceptions=True)
                    subscription=asyncio.create_task(push(result['cursor']))
        except (asyncio.IncompleteReadError,asyncio.TimeoutError,ConnectionError,protocol.ProtocolError,BridgeError,ssl.SSLError):pass
        except Exception:LOG.error('connection_failed')
        finally:
            if subscription:
                subscription.cancel();await asyncio.gather(subscription,return_exceptions=True)
            if admitted:self.active_peers[fingerprint]=max(0,self.active_peers.get(fingerprint,0)-1)
            writer.close()
            try:await writer.wait_closed()
            except Exception:pass
    async def route(self,peer,operation,args):
        if operation=='hello':
            require(args.get('version')==1,'VERSION_UNSUPPORTED')
            return {'protocolVersion':1,'gatewayVersion':'0.2.1','features':{'eventPush':True},'instanceId':self.store.meta('instanceId'),
                    'platforms':list({a.manifest['id']:a.manifest for a in self.adapters.values()}.values()),'chunkBytes':32768,'latestCursor':self.store.latest()}
        if operation=='ping':return {'now':int(time.time()*1000),'latestCursor':self.store.latest()}
        if operation=='accounts.list':return {'items':public(self.store.list('account',limit=100))}
        if operation in ('contacts.list','conversations.list'):
            kind='contact' if operation=='contacts.list' else 'conversation';offset=int(args.get('offset',0));require(0<=offset<=100000)
            rows=self.store.list(kind,limit=100,offset=offset,reverse=kind=='conversation');items=page(rows)
            return {'items':items,'nextOffset':offset+len(items) if len(rows)==100 or len(items)<len(rows) else None}
        if operation=='messages.list':
            cid=args.get('conversationId');require(isinstance(cid,str))
            before=args.get('before')
            if before:require(isinstance(before,dict) and isinstance(before.get('timestamp'),int) and isinstance(before.get('id'),str))
            rows=self.store.history(cid,before);items=page(rows)
            next_before={'timestamp':items[-1]['timestamp'],'id':items[-1]['id']} if items and (len(rows)==40 or len(items)<len(rows)) else None
            return {'items':items,'nextBefore':next_before}
        if operation=='sync.events':
            after=int(args.get('after',0));require(0<=after<=self.store.latest(),'INVALID_CURSOR')
            events=page(self.store.events(after,50))
            return {'events':events,'cursor':events[-1]['cursor'] if events else after,'latestCursor':self.store.latest()}
        if operation=='sync.subscribe':
            after=args.get('after');require(isinstance(after,int) and 0<=after<=self.store.latest(),'INVALID_CURSOR')
            return {'subscribed':True,'cursor':after}
        if operation=='modules.invoke':
            account=args.get('accountId');require(account in self.adapters,'ACCOUNT_NOT_FOUND')
            adapter=self.adapters[account];module=args.get('module');name=args.get('operation')
            require(isinstance(module,str) and module in adapter.manifest['modules'],'UNSUPPORTED_MODULE')
            require(isinstance(name,str),'INVALID_ARGUMENT')
            action=getattr(adapter,'module_operations',{}).get((module,name))
            require(action is not None,'UNSUPPORTED_OPERATION')
            require(adapter.manifest['capabilities'].get(action.capability,False),'UNSUPPORTED_CAPABILITY')
            params=args.get('params',{});require(isinstance(params,dict))
            params=action.validate(params);require(isinstance(params,dict))
            if action.read_only:
                result=public(await adapter.module_read(module,name,params))
                require(len(json.dumps(result,ensure_ascii=False).encode())<=110000,'RESULT_TOO_LARGE')
                return result
            # 新模块的副作用也走同一发送账本，不能因网络超时直接重放。
            key=args.get('commandId');require(isinstance(key,str) and re.fullmatch('[a-f0-9]{32}',key))
            request={'accountId':account,'module':module,'operation':name,'params':params}
            try:command,created=self.store.command(peer,key,request)
            except ValueError:raise BridgeError('IDEMPOTENCY_CONFLICT') from None
            if created:
                command=self.store.command_update(key,'queued',{**command,'peer':peer});self.work_events[account].set()
            return {'commandId':key,'status':command['status'],'messageId':command.get('messageId')}
        if operation=='messages.send':
            key=args.get('commandId');require(isinstance(key,str) and re.fullmatch('[a-f0-9]{32}',key))
            account=args.get('accountId');require(account in self.adapters,'ACCOUNT_NOT_FOUND')
            cid=args.get('conversationId');require(isinstance(cid,str))
            contact=self.store.get('contact',cid) or self.store.get('conversation',cid)
            require(contact is not None and contact['accountId']==account,'CONVERSATION_NOT_FOUND')
            kind=args.get('kind');require(kind in ('text','image','file'))
            request={'accountId':account,'conversationId':cid,'kind':kind}
            if kind=='text':
                text=args.get('text');require(isinstance(text,str) and 0<len(text.strip())<=16000)
                request['text']=text
            else:
                attachment=self.store.get('attachment',args.get('attachmentId',''))
                require(attachment is not None and attachment.get('ownerPeer')==peer,'ATTACHMENT_NOT_FOUND')
                # 新命令先拒绝未验证的文件名；旧编号仍按账本返回，不能因能力变化丢失查账结果。
                unicode_names=self.adapters[account].manifest['capabilities'].get('attachment.unicodeName',True)
                require(self.store.command_get(key,peer) is not None or unicode_names or attachment['name'].isascii(),'FILENAME_UNSUPPORTED','当前接入暂只支持英文、数字文件名，请重命名后再发送')
                request['attachmentId']=attachment['id']
            require(self.adapters[account].manifest['capabilities'].get(kind+'.send',False),'UNSUPPORTED_CAPABILITY')
            try:command,created=self.store.command(peer,key,request)
            except ValueError:raise BridgeError('IDEMPOTENCY_CONFLICT') from None
            if created:
                command=self.store.command_update(key,'queued',{**command,'peer':peer});self.work_events[account].set()
            return {'commandId':key,'status':command['status'],'messageId':command.get('messageId')}
        if operation=='commands.get':
            value=self.store.command_get(args.get('commandId',''),peer);require(value is not None,'COMMAND_NOT_FOUND')
            return {k:value.get(k) for k in ('id','status','messageId','error')}
        if operation=='attachments.begin':return self.begin_upload(peer,args)
        if operation=='attachments.chunk':return self.upload_chunk(peer,args)
        if operation=='attachments.commit':return self.commit_upload(peer,args)
        if operation=='attachments.read':
            aid=args.get('attachmentId','');require(isinstance(aid,str) and bool(re.fullmatch('[a-f0-9]{40}',aid)))
            info=self.store.get('attachment',aid);require(info is not None,'ATTACHMENT_NOT_FOUND')
            require(info.get('messageId') or info.get('ownerPeer')==peer,'ATTACHMENT_NOT_FOUND')
            offset=int(args.get('offset',0));require(0<=offset<=info['bytes'])
            data=self.read_blob(info);chunk=data[offset:offset+32768]
            return {'data':base64.b64encode(chunk).decode(),'offset':offset,'nextOffset':offset+len(chunk),'total':len(data),'sha256':info['sha256']}
        if operation=='attachments.retry':
            mid=args.get('messageId','');message=self.store.get('message',mid)
            require(message is not None and bool(message.get('attachments')),'MESSAGE_NOT_FOUND')
            with self.store.db:self.store.db.execute('INSERT OR REPLACE INTO media_jobs VALUES(?,0)',(mid,))
            return {'queued':True,'activeDownloadSupported':False}
        if operation=='diagnostics.get':
            return {'errors':self.errors,'latestCursor':self.store.latest(),'integratedAt':self.store.meta('integratedAt'),
                    'adapterPolling':{account:getattr(adapter,'poll_stats',{}) for account,adapter in self.adapters.items()},
                    'accounts':public(self.store.list('account')),'pendingCommands':len(self.store.commands('queued')),
                    'uncertainCommands':len(self.store.commands('uncertain')),'tls':'TLSv1.3','contentAtRest':'AES-256-GCM / DPAPI wrapped key'}
        raise BridgeError('UNSUPPORTED_OPERATION')

    def begin_upload(self,peer,args):
        name=args.get('name');length=args.get('bytes');digest=args.get('sha256');key=args.get('uploadKey')
        require(isinstance(name,str) and 1<=len(name)<=200 and safe_name(name)==name)
        require(isinstance(length,int) and 0<length<=32*1024*1024,'ATTACHMENT_TOO_LARGE')
        require(isinstance(digest,str) and bool(re.fullmatch('[a-f0-9]{64}',digest)))
        require(isinstance(key,str) and bool(re.fullmatch('[a-f0-9]{32}',key)))
        identifier=self.store.identity('upload',peer,key)
        row=self.store.db.execute('SELECT * FROM uploads WHERE id=?',(identifier,)).fetchone()
        value={'id':identifier,'name':name,'bytes':length,'sha256':digest,'contentType':args.get('contentType','application/octet-stream'),'createdAt':int(time.time())}
        if row:
            previous=self.store.decrypt('upload:'+identifier,row['payload'])
            require(all(previous.get(k)==value.get(k) for k in ('name','bytes','sha256')),'IDEMPOTENCY_CONFLICT')
            return {'uploadId':identifier,'received':row['received'],'chunkBytes':32768}
        with self.store.db:self.store.db.execute('INSERT INTO uploads VALUES(?,?,?,?)',(identifier,peer,0,self.store.encrypt('upload:'+identifier,value)))
        return {'uploadId':identifier,'received':0,'chunkBytes':32768}
    def upload(self,peer,args):
        identifier=args.get('uploadId');require(isinstance(identifier,str))
        row=self.store.db.execute('SELECT * FROM uploads WHERE id=? AND peer=?',(identifier,peer)).fetchone()
        require(row is not None,'UPLOAD_NOT_FOUND')
        return row,self.store.decrypt('upload:'+identifier,row['payload'])
    def upload_chunk(self,peer,args):
        row,value=self.upload(peer,args);offset=args.get('offset');encoded=args.get('data')
        require(isinstance(encoded,str) and len(encoded)<=44000 and isinstance(offset,int) and offset>=0 and offset%32768==0)
        try:data=base64.b64decode(encoded,validate=True)
        except ValueError:raise BridgeError('INVALID_ARGUMENT') from None
        require(0<len(data)<=32768 and offset+len(data)<=value['bytes'])
        require(len(data)==32768 or offset+len(data)==value['bytes'])
        if value.get('attachmentId'):
            info=self.store.get('attachment',value['attachmentId']);require(self.read_blob(info)[offset:offset+len(data)]==data,'IDEMPOTENCY_CONFLICT')
            return {'received':row['received']}
        chunk_id=self.store.identity('upload-chunk',value['id'],offset)
        if offset<row['received']:
            require(self.store.read_blob(chunk_id)==data,'IDEMPOTENCY_CONFLICT')
            return {'received':row['received']}
        require(offset==row['received'],'UPLOAD_OFFSET_MISMATCH')
        self.store.save_blob(chunk_id,data)
        with self.store.db:self.store.db.execute('UPDATE uploads SET received=? WHERE id=?',(offset+len(data),value['id']))
        return {'received':offset+len(data)}
    def commit_upload(self,peer,args):
        row,value=self.upload(peer,args)
        if value.get('attachmentId'):return public(self.store.get('attachment',value['attachmentId']))
        require(row['received']==value['bytes'],'UPLOAD_INCOMPLETE')
        data=b''.join(self.store.read_blob(self.store.identity('upload-chunk',value['id'],offset)) for offset in range(0,value['bytes'],32768))
        require(hashlib.sha256(data).hexdigest()==value['sha256'],'HASH_MISMATCH')
        aid=self.store.identity('uploaded-attachment',value['id']);info=self.store.save_blob(aid,data)
        attachment={**info,'name':value['name'],'contentType':value['contentType'],'state':'ready','md5':hashlib.md5(data).hexdigest(),'ownerPeer':peer}
        self.store.put('attachment',attachment)
        value['attachmentId']=aid
        with self.store.db:self.store.db.execute('UPDATE uploads SET payload=? WHERE id=?',(self.store.encrypt('upload:'+value['id'],value),value['id']))
        for offset in range(0,value['bytes'],32768):
            (self.store.root/'blobs'/self.store.identity('upload-chunk',value['id'],offset)).unlink(missing_ok=True)
        return public(attachment)

    async def poller(self,account,adapter):
        while not self.stopping.is_set():
            started=time.monotonic();failed=False
            try:
                await adapter.poll();self.errors.pop(account,None)
                if account not in self.ready_accounts:
                    self.store.set_meta('runtime.ready.'+account,{'bootId':self.boot_id,'at':time.time()});self.work_events[account].set()
                self.ready_accounts.add(account)
                current=self.store.get('account',account)
                if current and current.get('status')=='degraded':self.store.put('account',{**current,'status':'online','lastErrorCode':None})
            except Exception as error:
                failed=True
                self.ready_accounts.discard(account)
                self.store.set_meta('runtime.ready.'+account,None)
                code=error.code if isinstance(error,BridgeError) else 'ADAPTER_POLL_FAILED'
                self.errors[account]={'code':code,'at':int(time.time())}
                current=self.store.get('account',account)
                if current and current.get('status')!='loginRequired':self.store.put('account',{**current,'status':'degraded','lastErrorCode':code})
                LOG.warning('adapter_poll_failed account=%s code=%s',account,code)
            # 按起点计算周期，扫描耗时不再叠加一整段休眠；故障仍退避，避免忙重试。
            interval=max(.1,float(self.config.get('pollSeconds',.5)))
            await asyncio.sleep(max(1,interval) if failed else max(.05,interval-(time.monotonic()-started)))
    async def worker(self,account_id,adapter):
        while not self.stopping.is_set():
            wake=self.work_events[account_id];wake.clear()
            for command in [c for c in self.store.commands('queued') if c['request']['accountId']==account_id][:5]:
                account=self.store.get('account',adapter.account_id)
                if account_id not in self.ready_accounts or not account or account.get('status')!='online':continue
                try:
                    command=self.store.command_update(command['id'],'dispatching',command)
                    command=await adapter.send(command)
                    self.store.command_update(command['id'],'submitted',command)
                except Exception as error:
                    self.store.command_update(command['id'],'uncertain',{**command,'error':error.code if isinstance(error,BridgeError) else 'SEND_UNCERTAIN'})
            for state in ('submitted','uncertain'):
                for command in self.store.commands(state):
                    if command['request']['accountId']!=account_id:continue
                    if account_id not in self.ready_accounts:continue
                    if int(time.time())-command['createdAt']>86400:continue
                    if time.monotonic()<self.reconcile_due.get(command['id'],0):continue
                    age=int(time.time())-command.get('submittedAt',command['createdAt'])
                    self.reconcile_due[command['id']]=time.monotonic()+(.25 if state=='submitted' and age<10 else 1 if state=='submitted' else 15)
                    try:
                        result=await adapter.reconcile(command)
                        if result:self.store.command_accept(command['id'],result)
                        elif state=='submitted' and int(time.time())-command['createdAt']>60:self.store.command_update(command['id'],'uncertain',command)
                    except Exception:pass
            fresh=any(c['request']['accountId']==account_id for c in self.store.commands('submitted'))
            try:await asyncio.wait_for(wake.wait(),.25 if fresh else 3)
            except asyncio.TimeoutError:pass
    async def media_worker(self):
        while not self.stopping.is_set():
            jobs=self.store.db.execute('SELECT message_id FROM media_jobs WHERE next_attempt<=? ORDER BY next_attempt LIMIT 10',(int(time.time()),)).fetchall()
            for job in jobs:
                mid=job['message_id'];message=self.store.get('message',mid)
                if not message:continue
                if message['accountId'] not in self.ready_accounts:continue
                with self.store.db:self.store.db.execute('UPDATE media_jobs SET next_attempt=? WHERE message_id=?',(int(time.time())+30,mid))
                try:
                    value=await self.adapters[message['accountId']].media(message)
                    if value:
                        current=self.store.get('message',mid)
                        if current and current['attachments'][0].get('state')!='ready':
                            value={**current,'attachments':value['attachments']}
                            self.store.put('message',value,value['conversationId'],value['timestamp'])
                except Exception:LOG.warning('media_pending id=%s',mid)
                await asyncio.sleep(.1)
            await asyncio.sleep(2)
