"""Endpoint enrollment: portable encrypted transfer, Windows DPAPI at rest."""
import argparse,base64,datetime,getpass,json,os,re,subprocess,sys,tempfile
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from cryptography.x509.oid import NameOID,ExtendedKeyUsageOID
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'gateway/src'))
from chatbridge.security import protect

def lock_directory(path):
    path=Path(path);path.mkdir(parents=True,exist_ok=True)
    if os.name=='nt':
        import csv,io
        sid=list(csv.reader(io.StringIO(subprocess.check_output(['whoami','/user','/fo','csv','/nh'],text=True))))[0][1]
        subprocess.run(['icacls',str(path),'/inheritance:r','/grant:r',f'*{sid}:(OI)(CI)F','*S-1-5-18:(OI)(CI)F'],check=True,stdout=subprocess.DEVNULL)
    else:path.chmod(0o700)
    return path

def seal_transfer(value,password):
    if len(password)<12:raise ValueError('Use a transfer passphrase of at least 12 characters')
    salt=os.urandom(16);nonce=os.urandom(12);header=b'CBX1'+salt+nonce
    key=Scrypt(salt=salt,length=32,n=32768,r=8,p=1).derive(password.encode())
    return header+AESGCM(key).encrypt(nonce,json.dumps(value).encode(),header)

def open_transfer(data,password):
    if data[:4]!=b'CBX1' or len(data)<48:raise ValueError('Invalid encrypted enrollment')
    key=Scrypt(salt=data[4:20],length=32,n=32768,r=8,p=1).derive(password.encode())
    return json.loads(AESGCM(key).decrypt(data[20:32],data[32:],data[:32]))

def private_pem(key):return key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()
def cert_pem(cert):return cert.public_bytes(serialization.Encoding.PEM).decode()
def leaf(ca_key,ca_cert,name,server):
    key=ec.generate_private_key(ec.SECP256R1());now=datetime.datetime.now(datetime.timezone.utc)
    cert=x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,name)])).issuer_name(ca_cert.subject).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=10)).not_valid_after(now+datetime.timedelta(days=365)).add_extension(x509.BasicConstraints(ca=False,path_length=None),True).add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]),False).add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH if server else ExtendedKeyUsageOID.CLIENT_AUTH]),False).sign(ca_key,hashes.SHA256())
    return key,cert

def create_identity():
    key=ec.generate_private_key(ec.SECP256R1());name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'ChatBridge endpoint authority')]);now=datetime.datetime.now(datetime.timezone.utc)
    cert=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=10)).not_valid_after(now+datetime.timedelta(days=730)).add_extension(x509.BasicConstraints(ca=True,path_length=0),True).sign(key,hashes.SHA256())
    sk,sc=leaf(key,cert,'chatbridge-gateway',True);ck,cc=leaf(key,cert,'chatbridge-pc-b',False)
    return {'caPem':cert_pem(cert),'caKeyPem':private_pem(key),'serverCertPem':cert_pem(sc),'serverKeyPem':private_pem(sk),'clientCertPem':cert_pem(cc),'clientKeyPem':private_pem(ck),'serverFingerprint':sc.fingerprint(hashes.SHA256()).hex(),'clientFingerprint':cc.fingerprint(hashes.SHA256()).hex(),'storageKey':base64.b64encode(os.urandom(32)).decode(),'stcpSecret':os.urandom(32).hex()}

def read_dpapi(path):return json.loads(protect(Path(path).read_bytes(),True))
def write_dpapi(path,value):
    path=Path(path);temporary=None
    try:
        # 原子替换保留旧身份，掉电不能留下半份管理 CA。
        with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as output:
            temporary=Path(output.name);output.write(protect(json.dumps(value).encode()));output.flush();os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)
def export(bundle,output,password):
    pairing={'host':'127.0.0.1','port':19443,'serverName':'chatbridge-gateway',**{k:bundle[k] for k in ('caPem','clientCertPem','clientKeyPem','serverFingerprint')},'transport':bundle['transport']}
    write_dpapi(output/'pairing.dpapi',pairing)
    guest={k:bundle[k] for k in ('caPem','serverCertPem','serverKeyPem','storageKey','clientFingerprint')}
    guest.update(transport=bundle['transport'],frpToken=bundle.get('frpToken',''))
    (output/'guest-enrollment.cbx').write_bytes(seal_transfer(guest,password))
    (output/'authority-backup.cbx').write_bytes(seal_transfer(bundle,password))

def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='command',required=True)
    create=sub.add_parser('export');create.add_argument('--private-dir',type=Path,required=True);create.add_argument('--relay-host',required=True);create.add_argument('--relay-port',type=int,default=7000);create.add_argument('--relay-fingerprint',required=True);create.add_argument('--service-name',default='chatbridge-gateway');create.add_argument('--frp-token-file',type=Path);create.add_argument('--new-device',action='store_true')
    restore=sub.add_parser('restore-authority');restore.add_argument('--backup',type=Path,required=True);restore.add_argument('--private-dir',type=Path,required=True)
    args=parser.parse_args();folder=lock_directory(args.private_dir);authority=folder/'authority.dpapi'
    if args.command=='restore-authority':
        if authority.exists():raise RuntimeError('Existing authority is preserved; choose a fresh private directory')
        write_dpapi(authority,open_transfer(args.backup.read_bytes(),getpass.getpass('Backup passphrase: ')));print('Authority restored for the current Windows user');return
    if not re.fullmatch('[a-fA-F0-9]{64}',args.relay_fingerprint) or not 1<=args.relay_port<=65535:raise ValueError('A trusted SHA-256 relay fingerprint and valid port are required')
    bundle=read_dpapi(authority) if authority.exists() else create_identity()
    if args.new_device:
        ca=serialization.load_pem_private_key(bundle['caKeyPem'].encode(),None);cert=x509.load_pem_x509_certificate(bundle['caPem'].encode());key,leaf_cert=leaf(ca,cert,'chatbridge-pc-b',False)
        bundle.update(clientKeyPem=private_pem(key),clientCertPem=cert_pem(leaf_cert),clientFingerprint=leaf_cert.fingerprint(hashes.SHA256()).hex())
    bundle['transport']={'type':'frp-stcp','serverAddr':args.relay_host,'serverPort':args.relay_port,'serviceName':args.service_name,'secretKey':bundle['stcpSecret'],'relayFingerprint':args.relay_fingerprint.lower()}
    if args.frp_token_file:bundle['frpToken']=args.frp_token_file.read_text().strip()
    password=getpass.getpass('Transfer / backup passphrase (12+ characters): ')
    if len(password)<12 or password!=getpass.getpass('Repeat passphrase: '):raise ValueError('Use a matching passphrase of at least 12 characters')
    # 重复导出复用 CA 和数据密钥，节点替换不会把已有加密历史变成不可读。
    write_dpapi(authority,bundle);export(bundle,folder,password)
    print(json.dumps({'ok':True,'outputDirectory':str(folder),'clientFingerprint':bundle['clientFingerprint'],'chatKeysOnRelay':False}))
if __name__=='__main__':main()
