import asyncio,hashlib,json,re,struct,tempfile,os
from pathlib import Path
from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
from cryptography.hazmat.primitives.padding import PKCS7
from .adapters.wechat import sql_string

def safe_name(value):
    value=re.split(r'[/\\]',value)[-1]
    value=re.sub(r'[<>:"|?*\x00-\x1f]','_',value).strip('. ')
    if re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',value):value='_'+value
    return (value[:160] or 'attachment')

def decrypt_v2(payload,root):
    if not payload.startswith(b'\x07\x08V2\x08\x07') or len(payload)<31:return None
    own=re.sub(r'_[a-zA-Z0-9]{4}$','',root.name);profile=root
    while profile.parent.name!='Users' and profile.parent!=profile:profile=profile.parent
    directory=profile/'AppData'/'Roaming'/'Tencent'/'xwechat'/'net'/'kvcomm'
    aes_size,xor_size=struct.unpack_from('<II',payload,6);encrypted_size=aes_size+16-aes_size%16
    if 15+encrypted_size+xor_size>len(payload):return None
    for candidate in directory.glob('key_*.statistic'):
        match=re.match(r'key_(\d+)_',candidate.name)
        if not match:continue
        code=int(match[1]);key=hashlib.md5((str(code)+own).encode()).hexdigest()[:16].encode()
        try:
            decryptor=Cipher(algorithms.AES(key),modes.ECB()).decryptor()
            clear=decryptor.update(payload[15:15+encrypted_size])+decryptor.finalize()
            unpadder=PKCS7(128).unpadder();clear=unpadder.update(clear)+unpadder.finalize()
            if not clear.startswith((b'wxgf',b'\xff\xd8\xff',b'\x89PNG',b'GIF8')):continue
            end=len(payload)-xor_size
            return clear+payload[15+encrypted_size:end]+bytes(b^(code&255) for b in payload[end:])
        except ValueError:continue
    return None

async def worker(adapter,payload,mode):
    directory=Path(adapter.config['staging']);handle,path=tempfile.mkstemp(dir=directory);os.close(handle)
    path=Path(path);output=path.with_suffix('.result')
    try:
        path.write_bytes(payload)
        executable=adapter.config['voicePython'] if mode=='silk' else adapter.config['codecPython']
        process=await asyncio.create_subprocess_exec(executable,str(Path(__file__).with_name('media_worker.py')),mode,str(path),str(output),stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
        try:await asyncio.wait_for(process.wait(),45)
        except asyncio.TimeoutError:process.kill();await process.wait();return None
        if process.returncode or not output.exists():return None
        return output.read_bytes()
    finally:path.unlink(missing_ok=True);output.unlink(missing_ok=True)

async def resolve(adapter,message):
    source=message['_source'];row=source['row'];kind=message['kind'];root=Path(adapter.config['accountRoot'])
    native=source['native'];aid=message['attachments'][0]['id'];payload=None;content_type='application/octet-stream'
    state='ready';name='attachment';metadata={}
    if kind=='voice':
        names=await adapter.hook.query('media_0.db','SELECT rowid AS rid FROM Name2Id WHERE user_name='+sql_string(native)+' LIMIT 1')
        if not names:return None
        sql='SELECT voice_data FROM VoiceInfo WHERE chat_name_id='+str(int(names[0]['rid']))+' AND local_id='+str(int(row['local_id']))+' AND create_time>='+str(int(row['create_time'])-2)
        if str(row.get('server_id','0'))!='0':sql+=' AND svr_id='+str(int(row['server_id']))
        voices=await adapter.hook.query('media_0.db',sql+' LIMIT 2')
        if len(voices)!=1:return None
        original=bytes.fromhex(voices[0]['voice_data']);offset=original.find(b'#!SILK_V3')
        if not 0<=offset<=16:return None
        original_id=adapter.store.identity('original',aid);adapter.store.save_blob(original_id,original)
        payload=await worker(adapter,original[offset:],'silk')
        if payload is None:return None
        content_type='audio/wav';name='voice-'+str(row['local_id'])+'.wav';metadata={'originalBlobId':original_id,'sampleRate':16000,'seconds':(len(payload)-44)/32000}
    elif kind=='file':
        info=source.get('file',{});title=safe_name(info.get('title',''));name=title
        directory=root/'msg'/'file';candidates=[directory/title]
        if directory.exists():candidates.extend(p/title for p in directory.iterdir() if p.is_dir() and re.fullmatch(r'\d{4}-\d{2}',p.name))
        for path in candidates:
            if not path.is_file() or path.stat().st_size>64*1024*1024:continue
            data=await asyncio.to_thread(path.read_bytes)
            if len(data)==info.get('bytes') and hashlib.md5(data).hexdigest()==info.get('md5'):
                payload=data;break
    elif kind=='image':
        image=source.get('image',{});md5=image.get('md5','')
        if not re.fullmatch('[a-fA-F0-9]{32}',md5):return None
        links=await adapter.hook.query('hardlink.db','SELECT file_name FROM image_hardlink_info_v4 WHERE md5='+sql_string(md5)+' LIMIT 10')
        allowed={r['file_name'] for r in links}
        chats=await adapter.hook.query('message_resource.db','SELECT rowid AS rid FROM ChatName2Id WHERE user_name='+sql_string(native)+' LIMIT 1')
        if chats:
            resources=await adapter.hook.query('message_resource.db','SELECT packed_info FROM MessageResourceInfo WHERE chat_id='+str(int(chats[0]['rid']))+' AND message_local_id='+str(int(row['local_id']))+' AND message_svr_id='+str(int(row['server_id']))+' LIMIT 1')
            if resources:
                packed=bytes.fromhex(resources[0].get('packed_info') or '')
                if len(packed)==36 and packed[:4]==b'\x12\x22\x0a\x20':
                    stem=packed[4:].decode('ascii');allowed.update(stem+suffix for suffix in ('.dat','_t.dat','_b.dat','_h.dat'))
        directory=root/'msg'/'attach'/hashlib.md5(native.encode()).hexdigest();candidates=[]
        if directory.exists():
            for month in directory.iterdir():
                folder=month/'Img'
                if folder.is_dir():candidates.extend(p for p in folder.iterdir() if p.name in allowed and p.is_file())
        candidates.sort(key=lambda p:('_t.' in p.name or '_b.' in p.name,p.name))
        for path in candidates:
            if path.stat().st_size>16*1024*1024:continue
            decoded=await asyncio.to_thread(decrypt_v2,path.read_bytes(),root)
            if not decoded:continue
            exact=hashlib.md5(decoded).hexdigest()==md5
            if not exact and '_t.' not in path.name and '_b.' not in path.name:continue
            if decoded.startswith(b'wxgf'):decoded=await worker(adapter,decoded,'wxgf')
            if decoded is None:continue
            payload=decoded;state='ready' if exact else 'preview';content_type='image/png' if payload.startswith(b'\x89PNG') else 'image/jpeg'
            name='image-'+str(row['local_id'])+('.png' if content_type=='image/png' else '.jpg')
            metadata={'sourceMessageMd5Matched':exact,'sourceOriginalVerified':False}
            break
    if payload is None:return None
    info=adapter.store.save_blob(aid,payload)
    attachment={**info,'state':state,'kind':kind,'name':safe_name(name),'contentType':content_type,'messageId':message['id'],**metadata}
    adapter.store.put('attachment',attachment,parent=message['id'])
    return {**message,'attachments':[attachment]}
