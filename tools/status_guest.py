import argparse,base64,json,sqlite3,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'gateway/src'))
from chatbridge.security import load_secret
from chatbridge.store import Store
parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=Path(r'C:\ChatBridge'));args=parser.parse_args()
result={'ready':False,'accounts':[]}
try:
    config=json.loads((args.root/'private/gateway.json').read_text(encoding='utf-8-sig'))
    # 只读状态检查不能触发 Store 初始化时的命令恢复。
    store=Store.__new__(Store);store.key=base64.b64decode(load_secret(config['secretsFile'])['storageKey'])
    store.db=sqlite3.connect((Path(config['dataDirectory'])/'history.sqlite3').resolve().as_uri()+'?mode=ro',uri=True);store.db.row_factory=sqlite3.Row
    try:
        store.db.execute('BEGIN');runtime=store.meta('gateway.runtime',{})
        for account in store.list('account',limit=100):
            ready=store.meta('runtime.ready.'+account['id']) or {}
            result['accounts'].append({'id':account['id'],'status':account.get('status'),'currentProcess':bool(runtime.get('bootId')) and ready.get('bootId')==runtime.get('bootId')})
        result.update(gatewayPid=runtime.get('pid'),ready=bool(result['accounts']) and all(a['status']=='online' and a['currentProcess'] for a in result['accounts']))
    finally:store.close()
except Exception:result['code']='GATEWAY_STATUS_NOT_READY'
print(json.dumps(result))
