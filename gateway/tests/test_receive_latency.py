import asyncio,hashlib,time,types
import pytest
from chatbridge.adapters.wechat import WeChat
from chatbridge.server import Gateway
from test_reliability import store

def add_contact(store,adapter,name):
    cid=adapter.conversation_id(name)
    store.put('contact',{'id':cid,'accountId':adapter.account_id,'platformId':'wechat','name':name,'_native':name})
    return cid

def test_new_friend_change_precedes_pinned_idle_rechecks_and_bounds_background_work(store):
    async def check():
        adapter=WeChat(store,{'hookQueue':str(store.root),'backgroundCaptureLimit':1});adapter.refreshed=time.monotonic();adapter.started=0
        names=['pinned-'+str(i) for i in range(40)]+['friend'];now=int(time.time())
        # 新建运行器的单调时钟可能不足 30 秒，过期时间必须相对当前时钟设置。
        expired=time.monotonic()-31
        for name in names:add_contact(store,adapter,name);adapter.session_seen[name]=((now-10,1),expired)
        class Hook:
            async def query(self,db,sql):return [{'username':name,'last_timestamp':now-10,'last_msg_locald_id':2 if name=='friend' else 1} for name in names]
        adapter.hook=Hook();captured=[]
        async def capture(name,cid,force=False):captured.append(name)
        adapter.capture=capture;await adapter.poll()
        assert captured[0]=='friend' and len(captured)==2
        assert adapter.poll_stats['backgroundRemaining']==39
    asyncio.run(check())

def test_catalog_is_shared_across_conversations_and_empty_shards_are_not_reprobed(store):
    async def check():
        adapter=WeChat(store,{'hookQueue':str(store.root)});adapter.databases=['message_0.db','message_1.db','message_2.db'];adapter.started=0
        names=['friend1','friend2'];tables={'Msg_'+hashlib.md5(n.encode()).hexdigest() for n in names}
        class Hook:
            catalogs=0;messages=0
            async def query(self,db,sql):
                if 'sqlite_master' in sql:self.catalogs+=1;return [{'name':t} for t in tables] if db=='message_0.db' else []
                self.messages+=1;return []
        hook=Hook();adapter.hook=hook
        for _ in range(2):
            for name in names:assert await adapter.capture(name,add_contact(store,adapter,name),force=True)
        assert hook.catalogs==3 and hook.messages==4
    asyncio.run(check())

def test_missing_tables_do_not_starve_other_background_sessions(store):
    async def check():
        adapter=WeChat(store,{'hookQueue':str(store.root)});adapter.refreshed=time.monotonic();adapter.started=0
        for name in ['missing','valid']:add_contact(store,adapter,name)
        class Hook:
            async def query(self,db,sql):return [{'username':name,'last_timestamp':1,'last_msg_locald_id':1} for name in ['missing','valid']]
        adapter.hook=Hook();captured=[]
        async def capture(name,cid,force=False):captured.append(name);return name!='missing'
        adapter.capture=capture;await adapter.poll();await adapter.poll()
        assert captured==['missing','valid']
    asyncio.run(check())

def test_periodic_contacts_are_one_page_after_urgent_messages(store):
    async def check():
        adapter=WeChat(store,{'hookQueue':str(store.root),'contactRefreshSeconds':1});adapter.refreshed=time.monotonic()-2;adapter.started=0
        add_contact(store,adapter,'friend');adapter.session_seen['friend']=((1,1),time.monotonic())
        order=[]
        class Hook:
            async def call(self,args):return {'data':{'IsLogin':1,'dbs':[]}}
            async def query(self,db,sql):
                if db=='session.db':return [{'username':'friend','last_timestamp':2,'last_msg_locald_id':2}]
                order.append('contacts');return [{'username':'contact-'+str(i),'remark':'','nick_name':'','local_type':1,'delete_flag':0} for i in range(200)]
        async def capture(name,cid,force=False):order.append('message')
        adapter.hook=Hook();adapter.capture=capture;await adapter.poll()
        assert order==['message','contacts'] and adapter.contact_offset==200
    asyncio.run(check())

def test_poller_deducts_scan_time_from_interval_and_backs_off_on_failure(store,monkeypatch):
    import chatbridge.server as module
    async def check(fail):
        clock=[0.0];delays=[]
        class Adapter:
            account_id='test:receive';manifest={'id':'test'}
            async def poll(self):
                clock[0]+=.2
                if fail:raise RuntimeError('test failure')
        adapter=Adapter();server=Gateway(store,[adapter],{'pollSeconds':.15},{})
        async def sleep(delay):delays.append(delay);raise asyncio.CancelledError
        monkeypatch.setattr(module,'time',types.SimpleNamespace(monotonic=lambda:clock[0],time=time.time))
        monkeypatch.setattr(module.asyncio,'sleep',sleep)
        with pytest.raises(asyncio.CancelledError):await server.poller(adapter.account_id,adapter)
        return delays[0]
    assert asyncio.run(check(False))==.05
    assert asyncio.run(check(True))>=1
