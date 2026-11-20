import asyncio,json,os,struct

HEADER=struct.Struct('!4sBBBBII')
MAGIC=b'CBR1'
MAX_PAYLOAD=256*1024
MAX_PADDED=512*1024

class ProtocolError(Exception):pass

def encode(value):
    payload=json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
    if not 1<=len(payload)<=MAX_PAYLOAD:raise ProtocolError('FRAME_TOO_LARGE')
    # 分桶只减弱长度特征，实际保密与认证交给标准 TLS。
    padded=max(1024,1<<(len(payload)-1).bit_length())
    return HEADER.pack(MAGIC,1,0,1,0,len(payload),padded)+payload+os.urandom(padded-len(payload))

async def read(reader):
    raw=await reader.readexactly(HEADER.size)
    magic,major,minor,kind,flags,length,padded=HEADER.unpack(raw)
    if magic!=MAGIC or major!=1 or minor!=0 or kind!=1 or flags!=0:raise ProtocolError('UNSUPPORTED_FRAME')
    if not 1<=length<=MAX_PAYLOAD or not length<=padded<=MAX_PADDED:raise ProtocolError('INVALID_LENGTH')
    raw=await reader.readexactly(padded)
    try:value=json.loads(raw[:length].decode('utf-8'),parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
    except (ValueError,UnicodeError):raise ProtocolError('INVALID_JSON') from None
    if not isinstance(value,dict):raise ProtocolError('INVALID_ENVELOPE')
    return value

async def write(writer,value):
    writer.write(encode(value))
    await asyncio.wait_for(writer.drain(),15)
