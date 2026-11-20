import hashlib,hmac,json,os,sqlite3,time,uuid
from pathlib import Path
from .security import seal,open_seal

def serialized(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()

class Store:
    def __init__(self,root,key):
        self.on_change=None
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        (self.root/'blobs').mkdir(exist_ok=True);self.key=key
        self.db=sqlite3.connect(self.root/'history.sqlite3');self.db.row_factory=sqlite3.Row
        self.db.executescript('''PRAGMA journal_mode=WAL;PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS objects(kind TEXT,id TEXT,parent TEXT,sort INTEGER,payload BLOB,digest TEXT,PRIMARY KEY(kind,id));
        CREATE INDEX IF NOT EXISTS object_parent ON objects(kind,parent,sort);
        CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,payload BLOB);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,payload BLOB);
        CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,peer TEXT,digest TEXT,status TEXT,payload BLOB,updated INTEGER);
        CREATE TABLE IF NOT EXISTS uploads(id TEXT PRIMARY KEY,peer TEXT,received INTEGER,payload BLOB);
        CREATE TABLE IF NOT EXISTS media_jobs(message_id TEXT PRIMARY KEY,next_attempt INTEGER);
        CREATE TABLE IF NOT EXISTS receipts(receipt_id TEXT PRIMARY KEY,command_id TEXT UNIQUE);
        ''')
        if self.meta('integratedAt') is None:self.set_meta('integratedAt',int(time.time()))
        if not self.meta('receipts.initialized',False):
            with self.db:
                for row in self.db.execute("SELECT id,payload FROM commands WHERE status='serverAccepted' ORDER BY updated").fetchall():
                    value=self.decrypt('command:'+row['id'],row['payload']);receipt=value.get('messageId') or value.get('receiptId')
                    if receipt:self.db.execute('INSERT OR IGNORE INTO receipts VALUES(?,?)',(receipt,row['id']))
            self.set_meta('receipts.initialized',True)
        # 调用原生发送后崩溃无法证明是否送出，恢复时只查账，不再提交。
        for row in self.db.execute("SELECT id,payload FROM commands WHERE status='dispatching'").fetchall():
            value=self.decrypt('command:'+row['id'],row['payload']);value.update(status='uncertain')
            self.command_update(row['id'],'uncertain',value)

    def identity(self,*parts):return hmac.new(self.key,serialized(parts),hashlib.sha256).hexdigest()[:40]
    def encrypt(self,label,value):return seal(self.key,serialized(value),label.encode())
    def decrypt(self,label,value):return json.loads(open_seal(self.key,value,label.encode()))
    def meta(self,key,default=None):
        row=self.db.execute('SELECT payload FROM meta WHERE key=?',(key,)).fetchone()
        return self.decrypt('meta:'+key,row[0]) if row else default
    def set_meta(self,key,value):
        with self.db:self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,self.encrypt('meta:'+key,value)))
    def get(self,kind,key):
        row=self.db.execute('SELECT payload FROM objects WHERE kind=? AND id=?',(kind,key)).fetchone()
        return self.decrypt(kind+':'+key,row[0]) if row else None
    def put(self,kind,value,parent='',sort=0):
        key=value['id'];digest=hmac.new(self.key,serialized(value),hashlib.sha256).hexdigest()
        previous=self.db.execute('SELECT digest FROM objects WHERE kind=? AND id=?',(kind,key)).fetchone()
        if previous and previous[0]==digest:return False
        event={'type':kind+'.upsert','entity':value,'at':int(time.time()*1000)}
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO objects VALUES(?,?,?,?,?,?)',(kind,key,parent,sort,self.encrypt(kind+':'+key,value),digest))
            cursor=self.db.execute('INSERT INTO events(payload) VALUES(?)',(b'',)).lastrowid
            self.db.execute('UPDATE events SET payload=? WHERE seq=?',(self.encrypt('event:'+str(cursor),event),cursor))
            if kind=='message' and value.get('attachments'):
                if value['attachments'][0].get('state')=='ready':self.db.execute('DELETE FROM media_jobs WHERE message_id=?',(key,))
                else:self.db.execute('INSERT OR IGNORE INTO media_jobs VALUES(?,0)',(key,))
        if getattr(self,'on_change',None):self.on_change()
        return True
    def list(self,kind,parent=None,limit=100,offset=0,reverse=False):
        sql='SELECT id,payload FROM objects WHERE kind=?';args=[kind]
        if parent is not None:sql+=' AND parent=?';args.append(parent)
        sql+=' ORDER BY sort '+('DESC' if reverse else 'ASC')+',id LIMIT ? OFFSET ?';args.extend((limit,offset))
        return [self.decrypt(kind+':'+row['id'],row['payload']) for row in self.db.execute(sql,args)]
    def latest(self):return self.db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
    def history(self,cid,before=None,limit=40):
        sql="SELECT id,payload FROM objects WHERE kind='message' AND parent=?";args=[cid]
        if before:
            sql+=' AND (sort<? OR (sort=? AND id<?))';args.extend((before['timestamp'],before['timestamp'],before['id']))
        sql+=' ORDER BY sort DESC,id DESC LIMIT ?';args.append(limit)
        return [self.decrypt('message:'+r['id'],r['payload']) for r in self.db.execute(sql,args)]
    def events(self,after,limit):
        return [{'cursor':r['seq'],**self.decrypt('event:'+str(r['seq']),r['payload'])} for r in self.db.execute('SELECT seq,payload FROM events WHERE seq>? ORDER BY seq LIMIT ?',(after,limit))]
    def command(self,peer,key,request):
        digest=hmac.new(self.key,serialized(request),hashlib.sha256).hexdigest()
        row=self.db.execute('SELECT * FROM commands WHERE id=?',(key,)).fetchone()
        if row:
            if row['peer']!=peer or row['digest']!=digest:raise ValueError('IDEMPOTENCY_CONFLICT')
            return self.decrypt('command:'+key,row['payload']),False
        value={'id':key,'status':'queued','request':request,'createdAt':int(time.time())}
        with self.db:self.db.execute('INSERT INTO commands VALUES(?,?,?,?,?,?)',(key,peer,digest,'queued',self.encrypt('command:'+key,value),int(time.time())))
        return value,True
    def command_get(self,key,peer=None):
        row=self.db.execute('SELECT peer,payload FROM commands WHERE id=?',(key,)).fetchone()
        return self.decrypt('command:'+key,row['payload']) if row and (peer is None or peer==row['peer']) else None
    def command_update(self,key,status,value):
        value={**value,'status':status}
        with self.db:self.db.execute('UPDATE commands SET status=?,payload=?,updated=? WHERE id=?',(status,self.encrypt('command:'+key,value),int(time.time()),key))
        return value
    def receipt_available(self,receipt,command_id):
        row=self.db.execute('SELECT command_id FROM receipts WHERE receipt_id=?',(receipt,)).fetchone()
        return row is None or row[0]==command_id
    def command_accept(self,key,value):
        receipt=value.get('messageId') or value.get('receiptId')
        with self.db:
            if receipt:
                if not self.receipt_available(receipt,key):return False
                self.db.execute('INSERT OR IGNORE INTO receipts VALUES(?,?)',(receipt,key))
            accepted={**value,'status':'serverAccepted'}
            self.db.execute('UPDATE commands SET status=?,payload=?,updated=? WHERE id=?',('serverAccepted',self.encrypt('command:'+key,accepted),int(time.time()),key))
        # 原生消息与本地气泡按命令号合并，相同文案的两次发送仍是两条消息。
        message=self.get('message',receipt) if receipt else None
        if message:self.put('message',{**message,'commandId':key},message['conversationId'],message['timestamp'])
        return True
    def commands(self,status):
        return [self.decrypt('command:'+r['id'],r['payload']) for r in self.db.execute('SELECT id,payload FROM commands WHERE status=? ORDER BY updated',(status,))]
    def save_blob(self,identifier,payload):
        if len(payload)>64*1024*1024:raise ValueError('ATTACHMENT_TOO_LARGE')
        target=self.root/'blobs'/identifier
        temporary=target.with_suffix('.tmp');temporary.write_bytes(seal(self.key,payload,('blob:'+identifier).encode()));os.replace(temporary,target)
        return {'id':identifier,'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}
    def read_blob(self,identifier):return open_seal(self.key,(self.root/'blobs'/identifier).read_bytes(),('blob:'+identifier).encode())
    def close(self):self.db.close()
