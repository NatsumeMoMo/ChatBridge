import asyncio,base64,hashlib,json,os,sys
from pathlib import Path
import pytest
from cryptography.exceptions import InvalidTag
from chatbridge.store import Store
from chatbridge.server import Gateway,public
from chatbridge.errors import BridgeError
from chatbridge import protocol
from chatbridge.adapters.wechat import WeChat
from chatbridge.adapters.modules import ModuleOperation

@pytest.fixture
def store(tmp_path):
    value=Store(tmp_path,os.urandom(32));yield value;value.close()

class Adapter:
    account_id='test:one';manifest={'id':'test','name':'Test','capabilities':{'text.send':True,'image.send':False,'file.send':True}}

def gateway(store):return Gateway(store,[Adapter()],{}, {})
def run(gateway,op,args):return asyncio.run(gateway.route('peer',op,args))

def test_unsupported_filename_rejected_before_send_is_queued(store):
    adapter=Adapter();adapter.manifest={**Adapter.manifest,'capabilities':{**Adapter.manifest['capabilities'],'attachment.unicodeName':False}}
    server=Gateway(store,[adapter],{}, {})
    store.put('contact',{'id':'test-contact','accountId':adapter.account_id})
    for index,name in enumerate(('中文奇.txt','emoji-😀.txt')):
        aid='attachment-'+str(index);command_id=str(index)*32
        store.put('attachment',{'id':aid,'ownerPeer':'peer','name':name})
        request={'commandId':command_id,'accountId':adapter.account_id,'conversationId':'test-contact','kind':'file','attachmentId':aid}
        with pytest.raises(BridgeError) as rejected:run(server,'messages.send',request)
        assert rejected.value.code=='FILENAME_UNSUPPORTED'
        assert store.command_get(command_id) is None
    store.put('attachment',{'id':'ascii','ownerPeer':'peer','name':'chatbridge-test.txt'})
    request={**request,'commandId':'a'*32,'attachmentId':'ascii'}
    assert run(server,'messages.send',request)['status']=='queued'
    adapter.manifest['capabilities']['attachment.unicodeName']=True
    request={**request,'commandId':'b'*32,'attachmentId':'attachment-0'}
    assert run(server,'messages.send',request)['status']=='queued'
    existing=store.command_get('b'*32);store.command_update('b'*32,'uncertain',existing)
    adapter.manifest['capabilities']['attachment.unicodeName']=False
    assert run(server,'messages.send',request)['status']=='uncertain'

def test_encryption_authentication_and_no_plaintext(store):
    marker='private-message-01c3c3c8'
    store.put('message',{'id':'m','text':marker},'chat')
    assert store.get('message','m')['text']==marker
    assert all(marker.encode() not in file.read_bytes() for file in store.root.glob('history.sqlite3*'))
    store.save_blob('blob',marker.encode());target=store.root/'blobs'/'blob'
    data=bytearray(target.read_bytes());data[-1]^=1;target.write_bytes(data)
    with pytest.raises(InvalidTag):store.read_blob('blob')

def test_send_ledger_survives_restart_without_replay(store):
    value,created=store.command('peer','a'*32,{'text':'once'});assert created
    store.command_update(value['id'],'dispatching',value)
    other=Store(store.root,store.key)
    try:
        value,created=other.command('peer','a'*32,{'text':'once'})
        assert not created and value['status']=='uncertain'
        with pytest.raises(ValueError):other.command('peer','a'*32,{'text':'different'})
        with pytest.raises(ValueError):other.command('another-peer','a'*32,{'text':'once'})
    finally:other.close()

def test_platform_receipt_cannot_complete_two_different_commands(store):
    first,_=store.command('peer','a'*32,{'text':'same'})
    second,_=store.command('peer','b'*32,{'text':'same'})
    assert store.command_accept(first['id'],{**first,'messageId':'m'})
    assert not store.command_accept(second['id'],{**second,'messageId':'m'})
    assert store.command_get(second['id'])['status']=='queued'
    assert store.command_accept(first['id'],{**first,'messageId':'m'})


def test_login_flag_alone_does_not_mark_wechat_ready(store):
    adapter=WeChat(store,{'hookQueue':str(store.root)})
    store.put('account',{'id':adapter.account_id,'status':'loginRequired'})
    class Unready:
        async def call(self,data):return {'data':{'IsLogin':1,'dbs':[]}}
        async def query(self,database,sql):
            if database=='session.db':raise BridgeError('WECHAT_DATABASE_UNAVAILABLE')
            return []
    adapter.hook=Unready()
    with pytest.raises(BridgeError):asyncio.run(adapter.poll())
    assert store.get('account',adapter.account_id)['status']=='degraded'
    class Ready(Unready):
        async def call(self,data):return {'data':{'IsLogin':1,'dbs':[{'dbName':name} for name in ['contact.db','session.db','message_0.db']]}}
        async def query(self,database,sql):return []
    adapter.hook=Ready();asyncio.run(adapter.poll())
    assert store.get('account',adapter.account_id)['status']=='online'

def test_restart_does_not_dispatch_from_a_persisted_online_state(store):
    async def verify():
        polled=asyncio.Event();release=asyncio.Event();sent=asyncio.Event()
        class Restarting(Adapter):
            calls=0
            async def poll(self):
                polled.set();await release.wait()
            async def send(self,command):
                self.calls+=1;sent.set();return command
            async def reconcile(self,command):return None
        adapter=Restarting();server=Gateway(store,[adapter],{'pollSeconds':3600},{})
        store.put('account',{'id':adapter.account_id,'status':'online'})
        command,_=store.command('peer','d'*32,{'accountId':adapter.account_id,'kind':'text','text':'once'})
        first=asyncio.create_task(server.worker(adapter.account_id,adapter))
        poller=asyncio.create_task(server.poller(adapter.account_id,adapter))
        await polled.wait();await asyncio.sleep(.02)
        assert adapter.calls==0 and store.command_get(command['id'])['status']=='queued'
        first.cancel();await asyncio.gather(first,return_exceptions=True)
        release.set()
        for _ in range(10):
            if adapter.account_id in server.ready_accounts:break
            await asyncio.sleep(.01)
        second=asyncio.create_task(server.worker(adapter.account_id,adapter))
        try:
            await asyncio.wait_for(sent.wait(),.5)
            assert adapter.calls==1
        finally:
            second.cancel();poller.cancel();await asyncio.gather(second,poller,return_exceptions=True)
    asyncio.run(verify())

def test_events_do_not_advance_on_identical_updates_and_retain_previous_body(store):
    value={'id':'m','text':'original'};assert store.put('message',value,'c')
    assert not store.put('message',value,'c');cursor=store.latest()
    store.put('message',{'id':'m','text':'changed'},'c')
    events=store.events(0,100)
    assert len(events)==2 and events[0]['entity']['text']=='original'
    assert store.events(cursor,100)[0]['entity']['text']=='changed'

def test_history_keyset_does_not_skip_when_new_messages_arrive(store):
    for i in range(50):store.put('message',{'id':str(i).zfill(3),'timestamp':i,'text':str(i)},'chat',i)
    first=store.history('chat');last=first[-1]
    store.put('message',{'id':'new','timestamp':100,'text':'arrived during paging'},'chat',100)
    second=store.history('chat',{'timestamp':last['timestamp'],'id':last['id']})
    assert {m['id'] for m in first+second}=={str(i).zfill(3) for i in range(50)}

def test_media_jobs_survive_restart_and_complete_atomically(store):
    value={'id':'m','attachments':[{'id':'a','state':'pending'}]};store.put('message',value,'chat')
    other=Store(store.root,store.key)
    try:
        assert other.db.execute('SELECT COUNT(*) FROM media_jobs').fetchone()[0]==1
        other.put('message',{**value,'attachments':[{'id':'a','state':'ready'}]},'chat')
        assert other.db.execute('SELECT COUNT(*) FROM media_jobs').fetchone()[0]==0
    finally:other.close()

def test_upload_resume_duplicate_chunks_and_hash_verification(store):
    server=gateway(store);data=os.urandom(50000);args={'name':'test.bin','bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'uploadKey':'a'*32}
    upload=run(server,'attachments.begin',args);identifier=upload['uploadId']
    chunk={'uploadId':identifier,'offset':0,'data':base64.b64encode(data[:32768]).decode()}
    assert run(server,'attachments.chunk',chunk)['received']==32768
    assert run(server,'attachments.begin',args)['received']==32768
    assert run(server,'attachments.chunk',chunk)['received']==32768
    changed={**chunk,'data':base64.b64encode(b'0'*32768).decode()}
    with pytest.raises(BridgeError,match='IDEMPOTENCY_CONFLICT'):run(server,'attachments.chunk',changed)
    run(server,'attachments.chunk',{'uploadId':identifier,'offset':32768,'data':base64.b64encode(data[32768:]).decode()})
    result=run(server,'attachments.commit',{'uploadId':identifier})
    assert store.read_blob(result['id'])==data
    assert run(server,'attachments.commit',{'uploadId':identifier})['id']==result['id']
    assert run(server,'attachments.chunk',chunk)['received']==len(data)

@pytest.mark.parametrize('name',['../escape.txt','CON.txt','NUL','a/b.txt'])
def test_rejects_unsafe_windows_attachment_names(store,name):
    with pytest.raises(BridgeError):run(gateway(store),'attachments.begin',{'name':name,'bytes':1,'sha256':'a'*64,'uploadKey':'a'*32})

def test_attachment_cache_invalidates_when_preview_is_replaced(store):
    server=gateway(store);aid='a'*40
    first=store.save_blob(aid,b'preview');store.put('attachment',{**first,'ownerPeer':'peer'})
    assert base64.b64decode(run(server,'attachments.read',{'attachmentId':aid})['data'])==b'preview'
    second=store.save_blob(aid,b'complete-image');store.put('attachment',{**second,'ownerPeer':'peer'})
    assert base64.b64decode(run(server,'attachments.read',{'attachmentId':aid})['data'])==b'complete-image'

def test_device_ownership_and_capability_checks(store):
    server=gateway(store);store.put('contact',{'id':'c','accountId':'test:one','_native':'private'})
    args={'commandId':'b'*32,'accountId':'test:one','conversationId':'c','kind':'text','text':'hello'}
    assert run(server,'messages.send',args)['status']=='queued'
    assert run(server,'messages.send',args)['status']=='queued'
    assert len(store.commands('queued'))==1
    with pytest.raises(BridgeError,match='IDEMPOTENCY_CONFLICT'):run(server,'messages.send',{**args,'text':'changed'})
    assert public({'_native':'secret','text':'display','ownerPeer':'secret'})=={'text':'display'}

def test_source_identity_is_unique_across_conversations_and_malformed_xml_isolated(store):
    adapter=WeChat(store,{'hookQueue':str(store.root)})
    row={'local_id':'8','local_type':'1','server_id':'123','create_time':'123456','real_sender_id':'1','message_content':'hi'}
    a=adapter.normalize('message_0.db','friend1','c1',row,'friend1')
    b=adapter.normalize('message_0.db','friend2','c2',row,'friend2')
    assert a['id']!=b['id'] and a['direction']=='incoming'
    malicious={**row,'local_type':'3','message_content':'<!DOCTYPE x [<!ENTITY a "expanded">]><x>&a;</x>'}
    assert adapter.normalize('message_0.db','friend1','c1',malicious,'friend1')['kind']=='unsupported'

def test_frame_fragmentation_and_length_limit():
    async def verify():
        reader=asyncio.StreamReader();frame=protocol.encode({'a':'你好'})
        task=asyncio.create_task(protocol.read(reader))
        for i in range(0,len(frame),7):reader.feed_data(frame[i:i+7]);await asyncio.sleep(0)
        assert await task=={'a':'你好'}
        reader=asyncio.StreamReader();reader.feed_data(protocol.HEADER.pack(b'CBR1',1,0,1,0,1,0xffffffff))
        with pytest.raises(protocol.ProtocolError):await protocol.read(reader)
    asyncio.run(verify())

def test_nonchat_module_uses_capabilities_and_the_same_command_ledger(store):
    class Feed:
        account_id='demo:one'
        manifest={'id':'demo','name':'Demo','modules':['feed'],'capabilities':{'feed.read':True,'feed.save':True}}
        module_operations={('feed','list'):ModuleOperation('feed.read',True,lambda p:p),('feed','save'):ModuleOperation('feed.save',False,lambda p:p)}
        async def module_read(self,module,operation,params):return {'items':[{'id':'post','title':'example'}]}
    server=Gateway(store,[Feed()],{},{});args={'accountId':'demo:one','module':'feed','operation':'list'}
    assert run(server,'modules.invoke',args)['items'][0]['id']=='post'
    write={**args,'operation':'save','params':{'id':'post'},'commandId':'c'*32}
    assert run(server,'modules.invoke',write)['status']=='queued'
    assert run(server,'modules.invoke',write)['status']=='queued' and len(store.commands('queued'))==1
    with pytest.raises(BridgeError,match='UNSUPPORTED_MODULE'):run(server,'modules.invoke',{**args,'module':'unknown'})

def test_new_command_wakes_idle_worker_without_replaying_duplicates(store):
    async def verify():
        sent=asyncio.Event()
        class Fast(Adapter):
            calls=0
            async def send(self,command):self.calls+=1;sent.set();return command
            async def reconcile(self,command):return command
        adapter=Fast();server=Gateway(store,[adapter],{},{});server.ready_accounts.add(adapter.account_id)
        store.put('account',{'id':adapter.account_id,'status':'online'});store.put('contact',{'id':'c','accountId':adapter.account_id})
        worker=asyncio.create_task(server.worker(adapter.account_id,adapter));await asyncio.sleep(.02)
        request={'commandId':'e'*32,'accountId':adapter.account_id,'conversationId':'c','kind':'text','text':'once'}
        try:
            await server.route('peer','messages.send',request);await asyncio.wait_for(sent.wait(),.5)
            await server.route('peer','messages.send',request);assert adapter.calls==1
        finally:worker.cancel();await asyncio.gather(worker,return_exceptions=True)
    asyncio.run(verify())

def test_event_subscription_replays_and_does_not_block_requests(store):
    async def verify():
        server=gateway(store);server.authorized=lambda _:True
        incoming=asyncio.StreamReader();outgoing=asyncio.StreamReader()
        class SSL:
            def getpeercert(self,binary_form=False):return b'certificate'
        class Writer:
            closed=False
            def get_extra_info(self,name):return SSL()
            def write(self,data):outgoing.feed_data(data)
            async def drain(self):pass
            def close(self):self.closed=True
            async def wait_closed(self):pass
        writer=Writer();connection=asyncio.create_task(server.connection(incoming,writer))
        async def request(identifier,op,args):
            incoming.feed_data(protocol.encode({'type':'request','id':identifier,'op':op,'args':args}))
            return await asyncio.wait_for(protocol.read(outgoing),.5)
        try:
            assert (await request('1','hello',{'version':1}))['ok']
            assert (await request('2','sync.subscribe',{'after':0}))['ok']
            assert (await request('3','ping',{}))['id']=='3'
            store.put('message',{'id':'m','text':'first'},'c')
            event=await asyncio.wait_for(protocol.read(outgoing),.5);assert event['type']=='events' and event['events'][0]['entity']['id']=='m'
            cursor=event['cursor']
            assert (await request('4','sync.subscribe',{'after':cursor}))['ok'] and not writer.closed
            store.put('message',{'id':'m','text':'revised'},'c')
            event=await asyncio.wait_for(protocol.read(outgoing),.5);assert event['cursor']>cursor and event['events'][0]['entity']['text']=='revised'
            server.authorized=lambda _:False;store.put('message',{'id':'private','text':'revoked'},'c')
            await asyncio.sleep(.02);assert writer.closed
        finally:
            incoming.feed_eof();await asyncio.wait_for(connection,.5)
    asyncio.run(verify())

def test_receipt_correlation_survives_wechat_refresh(store):
    adapter=WeChat(store,{'hookQueue':str(store.root)})
    row={'local_id':'8','local_type':'1','server_id':'123','create_time':'123456','real_sender_id':'1','message_content':'same'}
    message=adapter.normalize('message_0.db','friend1','c1',row,'self');store.put('message',message,'c1',message['timestamp'])
    command,_=store.command('peer','a'*32,{'text':'same'})
    store.command_accept(command['id'],{**command,'messageId':message['id']})
    assert adapter.normalize('message_0.db','friend1','c1',row,'self')['commandId']==command['id']

def test_idle_chat_skips_redundant_queries_but_same_second_new_id_is_captured(store):
    async def verify():
        import time
        adapter=WeChat(store,{'hookQueue':str(store.root)});adapter.refreshed=time.monotonic();adapter.started=0;adapter.databases=['message_0.db']
        cid=adapter.conversation_id('friend');store.put('contact',{'id':cid,'accountId':adapter.account_id,'_native':'friend'})
        class Database:
            local=1;captures=0
            async def query(self,database,sql):
                if database=='session.db':return [{'username':'friend','last_timestamp':'123456','last_msg_locald_id':str(self.local)}]
                if 'sqlite_master' in sql:return [{'name':'Msg_'+hashlib.md5(b'friend').hexdigest()}]
                if 'SELECT m.' in sql:
                    self.captures+=1
                    return [{'local_id':str(self.local),'local_type':'1','server_id':str(self.local),'create_time':'123456','real_sender_id':'1','sender_name':'friend','message_content':'sample'}]
                raise AssertionError(sql)
        database=Database();adapter.hook=database
        await adapter.poll();await adapter.poll();assert database.captures==1
        database.local=2;await adapter.poll();assert database.captures==2
        assert len(store.history(cid))==2
    asyncio.run(verify())
