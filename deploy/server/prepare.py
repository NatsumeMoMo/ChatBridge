"""Generate relay-only credentials. No endpoint or message keys exist here."""
import argparse,datetime,hashlib,json,os
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
parser=argparse.ArgumentParser();parser.add_argument('--output-dir',type=Path,required=True);parser.add_argument('--port',type=int,default=7000);args=parser.parse_args()
folder=args.output_dir.resolve()
if folder.exists() and any(folder.iterdir()):raise RuntimeError('Preserve existing relay credentials; use an empty directory')
if not 1<=args.port<=65535:raise ValueError('Invalid relay port')
folder.mkdir(parents=True,exist_ok=True)
if os.name!='nt':folder.chmod(0o700)
key=ec.generate_private_key(ec.SECP256R1());name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'frp')]);now=datetime.datetime.now(datetime.timezone.utc)
cert=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=10)).not_valid_after(now+datetime.timedelta(days=730)).add_extension(x509.SubjectAlternativeName([x509.DNSName('frp')]),False).sign(key,hashes.SHA256())
(folder/'tls.crt').write_bytes(cert.public_bytes(serialization.Encoding.PEM));(folder/'tls.key').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
token=os.urandom(32).hex();(folder/'frp-token.txt').write_text(token)
config=f'bindPort = {args.port}\nauth.method = "token"\nauth.token = {json.dumps(token)}\ntransport.tls.force = true\ntransport.tls.certFile = "/etc/chatbridge-relay/tls.crt"\ntransport.tls.keyFile = "/etc/chatbridge-relay/tls.key"\nlog.to = "console"\nlog.level = "warn"\n'
(folder/'frps.toml').write_text(config)
fingerprint=cert.fingerprint(hashes.SHA256()).hex();(folder/'relay-public.json').write_text(json.dumps({'port':args.port,'tlsFingerprint':fingerprint},indent=2))
for file in folder.iterdir():
    if os.name!='nt':file.chmod(0o600)
print(json.dumps({'ok':True,'relayFingerprint':fingerprint,'port':args.port,'chatKeysPresent':False}))
