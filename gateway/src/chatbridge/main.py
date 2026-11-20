import argparse,asyncio,base64,ctypes,json,logging,os,signal,sys,uuid
from pathlib import Path
from .adapters import create_adapter
from logging.handlers import RotatingFileHandler
from .security import load_secret
from .server import Gateway
from .store import Store

async def serve(config):
    directory=Path(config['dataDirectory']);directory.mkdir(parents=True,exist_ok=True)
    Path(config['staging']).mkdir(parents=True,exist_ok=True)
    guard=open(directory/'gateway.lock','a+b')
    if os.name=='nt':
        import msvcrt
        guard.seek(0);guard.write(b'0');guard.flush();guard.seek(0);msvcrt.locking(guard.fileno(),msvcrt.LK_NBLCK,1)
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    secrets=load_secret(config['secretsFile']);store=Store(directory,base64.b64decode(secrets['storageKey']))
    if store.meta('instanceId') is None:store.set_meta('instanceId',uuid.uuid4().hex)
    adapters=[]
    for entry in config['adapters']:
        adapters.append(create_adapter(store,entry,{'staging':config['staging']}))
    if len({a.account_id for a in adapters})!=len(adapters):raise ValueError('Duplicate account ID')
    gateway=Gateway(store,adapters,config,secrets);server=await gateway.start()
    logging.info('gateway_started protocol=1 tls=1.3')
    try:
        async with server:await server.serve_forever()
    finally:
        await gateway.close();store.close();guard.close()
        if os.name=='nt':ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);args=parser.parse_args()
    config=json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
    handlers=[RotatingFileHandler(config['logFile'],maxBytes=5*1024*1024,backupCount=3,encoding='utf-8')] if config.get('logFile') else None
    logging.basicConfig(handlers=handlers,level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    try:asyncio.run(serve(config))
    except KeyboardInterrupt:pass
    except Exception:logging.error('gateway_stopped code=STARTUP_OR_RUNTIME_FAILURE');raise

if __name__=='__main__':main()
