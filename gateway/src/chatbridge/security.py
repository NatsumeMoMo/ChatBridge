import ctypes,json,os,tempfile
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

def protect(data,unprotect=False):
    if os.name!='nt':raise RuntimeError('Windows DPAPI is required for deployed key storage')
    class Blob(ctypes.Structure):_fields_=[('size',ctypes.c_uint32),('data',ctypes.POINTER(ctypes.c_ubyte))]
    buffer=ctypes.create_string_buffer(data)
    source=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));output=Blob()
    function=ctypes.windll.crypt32.CryptUnprotectData if unprotect else ctypes.windll.crypt32.CryptProtectData
    if not function(ctypes.byref(source),None,None,None,None,1,ctypes.byref(output)):raise ctypes.WinError()
    try:return ctypes.string_at(output.data,output.size)
    finally:ctypes.windll.kernel32.LocalFree(output.data)

def load_secret(path):return json.loads(protect(Path(path).read_bytes(),True))

def seal(key,data,aad):
    nonce=os.urandom(12)
    return nonce+AESGCM(key).encrypt(nonce,data,aad)

def open_seal(key,data,aad):return AESGCM(key).decrypt(data[:12],data[12:],aad)

def load_tls_chain(context,certificate,key,directory):
    paths=[]
    try:
        # OpenSSL 需读取文件，装载后立即删除，仅保留进程内的私钥。
        for value in (certificate,key):
            fd,path=tempfile.mkstemp(dir=directory,suffix='.pem');paths.append(path)
            with os.fdopen(fd,'wb') as output:output.write(value.encode())
        context.load_cert_chain(*paths)
    finally:
        for path in paths:Path(path).unlink(missing_ok=True)
