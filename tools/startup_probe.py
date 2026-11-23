"""Fast, read-only startup health. No WMI providers, message bodies or sends."""
import argparse,base64,ctypes,json,os,socket,sqlite3,sys,time
from ctypes import wintypes as W
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'gateway/src'))
from chatbridge.security import load_secret
from chatbridge.store import Store

def same_path(a,b):return os.path.normcase(os.path.abspath(a))==os.path.normcase(os.path.abspath(b))
def read_json(path,default=None):
    try:return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):return default

class Windows:
    def __init__(self):
        if os.name!='nt':raise OSError('Windows is required')
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.kernel.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];self.kernel.OpenProcess.restype=W.HANDLE
        self.kernel.CloseHandle.argtypes=[W.HANDLE]
        self.kernel.QueryFullProcessImageNameW.argtypes=[W.HANDLE,W.DWORD,W.LPWSTR,ctypes.POINTER(W.DWORD)]
        self.kernel.GetProcessTimes.argtypes=[W.HANDLE]+[ctypes.POINTER(W.FILETIME)]*4
        self.kernel.CreateToolhelp32Snapshot.argtypes=[W.DWORD,W.DWORD];self.kernel.CreateToolhelp32Snapshot.restype=W.HANDLE

    def process(self,pid):
        handle=self.kernel.OpenProcess(0x1000,False,pid)
        if not handle:return None
        try:
            size=W.DWORD(32768);buffer=ctypes.create_unicode_buffer(size.value)
            if not self.kernel.QueryFullProcessImageNameW(handle,0,buffer,ctypes.byref(size)):return None
            times=[W.FILETIME() for _ in range(4)]
            if not self.kernel.GetProcessTimes(handle,*[ctypes.byref(v) for v in times]):return None
            started=((times[0].dwHighDateTime<<32)+times[0].dwLowDateTime)/10_000_000-11644473600
            return {'pid':pid,'path':buffer.value,'startedAt':started}
        finally:self.kernel.CloseHandle(handle)

    def processes(self,names):
        class Entry(ctypes.Structure):
            _fields_=[('size',W.DWORD),('usage',W.DWORD),('pid',W.DWORD),('heap',ctypes.c_size_t),('module',W.DWORD),('threads',W.DWORD),('parent',W.DWORD),('priority',W.LONG),('flags',W.DWORD),('name',W.WCHAR*260)]
        snapshot=self.kernel.CreateToolhelp32Snapshot(2,0)
        if snapshot in (None,ctypes.c_void_p(-1).value):raise ctypes.WinError(ctypes.get_last_error())
        self.kernel.Process32FirstW.argtypes=[W.HANDLE,ctypes.POINTER(Entry)];self.kernel.Process32NextW.argtypes=[W.HANDLE,ctypes.POINTER(Entry)]
        found=[];entry=Entry();entry.size=ctypes.sizeof(entry)
        try:
            more=self.kernel.Process32FirstW(snapshot,ctypes.byref(entry))
            while more:
                if entry.name.lower() in names:
                    process=self.process(entry.pid)
                    if process:found.append(process)
                more=self.kernel.Process32NextW(snapshot,ctypes.byref(entry))
        finally:self.kernel.CloseHandle(snapshot)
        return found

    def tcp(self):
        library=ctypes.WinDLL('iphlpapi');function=library.GetExtendedTcpTable
        function.argtypes=[ctypes.c_void_p,ctypes.POINTER(W.DWORD),W.BOOL,W.ULONG,ctypes.c_int,W.ULONG]
        class Row4(ctypes.Structure):_fields_=[('state',W.DWORD),('local',W.DWORD),('localPort',W.DWORD),('remote',W.DWORD),('remotePort',W.DWORD),('pid',W.DWORD)]
        class Row6(ctypes.Structure):_fields_=[('local',W.BYTE*16),('localScope',W.DWORD),('localPort',W.DWORD),('remote',W.BYTE*16),('remoteScope',W.DWORD),('remotePort',W.DWORD),('state',W.DWORD),('pid',W.DWORD)]
        rows=[]
        for family,row_type in [(2,Row4),(23,Row6)]:
            size=W.DWORD();result=function(None,ctypes.byref(size),False,family,5,0)
            if result not in (0,122):
                if family==23:continue
                raise OSError(result,'TCP status unavailable')
            for _ in range(3):
                buffer=ctypes.create_string_buffer(max(4,size.value));result=function(buffer,ctypes.byref(size),False,family,5,0)
                if result!=122:break
            if result:raise OSError(result,'TCP status unavailable')
            count=W.DWORD.from_buffer(buffer).value
            for i in range(count):
                row=row_type.from_buffer_copy(buffer,4+i*ctypes.sizeof(row_type))
                rows.append({'pid':row.pid,'state':row.state,'localPort':socket.ntohs(row.localPort&0xffff)})
        return rows

def endpoint_config(root):
    profile=read_json(root/'private/profile.json')
    if profile:return {'client':profile['wechatExe'],'clientTask':'ChatBridge-Client','files':[(profile['wechatExe'],profile['hashes']['launcher']),(profile['wechatCore'],profile['hashes']['core']),(profile['hookDll'],profile['hashes']['hook'])]}
    pinned=Path(r'C:\ChatBridgeValidation\runtime\Weixin4110');pins=read_json(root/'private/client-pin.json')
    if not pins:raise ValueError('Pinned client configuration is missing')
    return {'client':str(pinned/'Weixin.exe'),'clientTask':'ChatBridgeValidation-WeChat4Launch','files':[(str(pinned/'Weixin.exe'),pins['launcherSha256']),(str(pinned/'4.1.10.27/Weixin.dll'),pins['coreSha256']),(str(pinned/'version.dll'),pins['hookSha256'])]}

def read_gateway(root):
    config=read_json(root/'private/gateway.json')
    if not config:raise ValueError('Gateway configuration is missing')
    store=Store.__new__(Store);store.key=base64.b64decode(load_secret(config['secretsFile'])['storageKey'])
    store.db=sqlite3.connect((Path(config['dataDirectory'])/'history.sqlite3').resolve().as_uri()+'?mode=ro',uri=True);store.db.row_factory=sqlite3.Row
    try:
        store.db.execute('BEGIN');runtime=store.meta('gateway.runtime',{});accounts=[]
        for account in store.list('account',limit=100):
            ready=store.meta('runtime.ready.'+account['id']) or {}
            accounts.append({'status':account.get('status'),'code':account.get('lastErrorCode'),'currentProcess':bool(runtime.get('bootId')) and ready.get('bootId')==runtime.get('bootId')})
        return config,runtime,accounts
    finally:store.close()

def current_gateway(runtime,process):
    # 重启后 PID 会复用，必须同时核对这次进程的创建时间，不能只相信旧的在线记录。
    return bool(process and runtime.get('pid')==process['pid'] and 0<=runtime.get('startedAt',0)-process['startedAt']<=30)

def probe(root,windows=None):
    started=time.monotonic();windows=windows or Windows();endpoint=endpoint_config(root)
    result={'ready':False,'client':{'alive':False,'verified':False},'gateway':{'alive':False,'ready':False,'listening':False},'relay':{'alive':False,'connected':False},'accounts':[],'clientTask':endpoint['clientTask']}
    processes=windows.processes({'weixin.exe','frpc.exe'});guard=read_json(root/'private/client-start-state.json',{})
    clients=[p for p in processes if same_path(p['path'],endpoint['client'])]
    if clients:
        client=next((p for p in clients if p['pid']==guard.get('pid')),min(clients,key=lambda p:p['startedAt']))
        result['client']={'alive':True,'pid':client['pid'],'verified':guard.get('phase')=='running-verified' and guard.get('pid')==client['pid']}
    relays=[p for p in processes if same_path(p['path'],str(root/'bin/frpc.exe'))]
    rows=windows.tcp();result['relay']={'alive':bool(relays),'connected':any(row['pid'] in {p['pid'] for p in relays} and row['state']==5 for row in rows),'pids':[p['pid'] for p in relays]}
    try:
        config,runtime,accounts=read_gateway(root);process=windows.process(int(runtime.get('pid') or 0))
        allowed=[str(root/'runtime/Scripts/python.exe'),sys.executable,getattr(sys,'_base_executable',sys.executable)]
        alive=current_gateway(runtime,process) and any(same_path(process['path'],path) for path in allowed)
        listening=bool(alive and any(row['pid']==runtime['pid'] and row['state']==2 and row['localPort']==config.get('port',19443) for row in rows))
        online=bool(accounts) and all(a['status']=='online' and a['currentProcess'] for a in accounts)
        result['accounts']=accounts;result['gateway']={'alive':alive,'listening':listening,'ready':listening and online,'pid':runtime.get('pid')}
    except Exception as error:result['gateway']['code']=type(error).__name__
    result['loginRequired']=any(a['status']=='loginRequired' for a in result['accounts'])
    result['ready']=result['gateway']['ready'] and result['relay']['connected'] and result['client']['verified']
    result['durationMs']=round((time.monotonic()-started)*1000,1)
    return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=Path(r'C:\ChatBridge'));parser.add_argument('--output',type=Path);args=parser.parse_args()
    try:result=probe(args.root.resolve())
    except Exception as error:result={'ready':False,'error':type(error).__name__,'detail':str(error)}
    text=json.dumps(result)
    if args.output:args.output.write_text(text,encoding='utf-8')
    else:print(text)
if __name__=='__main__':main()
