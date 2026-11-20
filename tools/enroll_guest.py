"""Run inside the VM as the account that owns the gateway task."""
import argparse,getpass,json,sys
from pathlib import Path
from enrollment import lock_directory,open_transfer,read_dpapi,write_dpapi

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=Path(r'C:\ChatBridge'));parser.add_argument('--enrollment',type=Path,required=True);parser.add_argument('--reconfigure',action='store_true');args=parser.parse_args()
    root=args.root.resolve();private=lock_directory(root/'private');path=private/'secrets.dpapi'
    value=open_transfer(args.enrollment.read_bytes(),getpass.getpass('Transfer passphrase: '))
    secrets={k:value[k] for k in ('caPem','serverCertPem','serverKeyPem','storageKey')}
    if path.exists():
        if not args.reconfigure:raise RuntimeError('Existing gateway keys are preserved; use --reconfigure for relay / device changes')
        current=read_dpapi(path)
        if current!=secrets:raise RuntimeError('Enrollment does not match this gateway; refusing to replace keys')
    else:write_dpapi(path,secrets)
    devices=private/'devices.json';allowed=json.loads(devices.read_text(encoding='utf-8-sig')).get('allowed',[]) if devices.exists() else []
    if value['clientFingerprint'] not in allowed:allowed.append(value['clientFingerprint'])
    devices.write_text(json.dumps({'allowed':allowed},indent=2),encoding='utf-8')
    t=value['transport'];quoted=lambda x:json.dumps(str(x),ensure_ascii=False)
    lines=['serverAddr = '+quoted(t['serverAddr']),'serverPort = '+str(int(t['serverPort'])),'loginFailExit = false','auth.method = "token"','auth.token = '+quoted(value.get('frpToken','')),'transport.tls.enable = true','log.to = '+quoted((private/'frpc.log').as_posix()),'log.level = "warn"','log.maxDays = 7','[[proxies]]','name = '+quoted(t['serviceName']),'type = "stcp"','secretKey = '+quoted(t['secretKey']),'localIP = "127.0.0.1"','localPort = 19443','transport.useCompression = false']
    config=private/'frpc.toml'
    if config.exists():config.with_suffix('.toml.previous').write_bytes(config.read_bytes())
    config.write_text('\n'.join(lines)+'\n',encoding='utf-8');print(json.dumps({'ok':True,'allowedDevices':len(allowed),'keysPreserved':args.reconfigure}))
if __name__=='__main__':main()
