import base64,json,sqlite3,sys
from pathlib import Path
root=Path(r'C:\ChatBridge');sys.path.insert(0,str(root/'app'/'gateway'/'src'))
from chatbridge.security import load_secret
from chatbridge.store import Store
result={'ready':False,'accounts':[]}
try:
    # 检查不能触发网关初始化或修改正在工作的命令账本。
    store=Store.__new__(Store);store.root=root/'data'
    store.key=base64.b64decode(load_secret(root/'private'/'secrets.dpapi')['storageKey'])
    store.db=sqlite3.connect('file:C:/ChatBridge/data/history.sqlite3?mode=ro',uri=True);store.db.row_factory=sqlite3.Row
    try:
        store.db.execute('BEGIN');runtime=store.meta('gateway.runtime',{})
        for account in store.list('account',limit=100):
            ready=store.meta('runtime.ready.'+account['id']) or {}
            result['accounts'].append({'id':account['id'],'status':account.get('status'),'code':account.get('lastErrorCode'),'currentProcess':bool(runtime.get('bootId')) and ready.get('bootId')==runtime.get('bootId')})
        result.update(gatewayPid=runtime.get('pid'),bootId=runtime.get('bootId'),ready=bool(result['accounts']) and all(a['status']=='online' and a['currentProcess'] for a in result['accounts']))
    finally:store.close()
except Exception:result['code']='GATEWAY_STATUS_NOT_READY'
print(json.dumps(result))
