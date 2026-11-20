import io,struct,sys,wave,zlib
from pathlib import Path

mode,source,destination=sys.argv[1:];data=Path(source).read_bytes()
if mode=='silk':
    import pilk
    raw=Path(destination+'.pcm')
    try:
        pilk.decode(source,str(raw))
        pcm=raw.read_bytes()
        with wave.open(destination,'wb') as output:
            output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000);output.writeframes(pcm)
    finally:raw.unlink(missing_ok=True)
elif mode=='wxgf':
    import av
    offset=data.find(b'\0\0\0\1')
    if offset<0:offset=data.find(b'\0\0\1')
    if offset<0:raise ValueError('No HEVC stream')
    with av.open(io.BytesIO(data[offset:]),format='hevc') as container:
        frame=next(container.decode(video=0))
        if frame.width*frame.height>24000000:raise ValueError('Image dimensions exceed limit')
        rgb=frame.to_ndarray(format='rgb24');raw=b''.join(b'\0'+row.tobytes() for row in rgb)
        def chunk(kind,payload):return struct.pack('!I',len(payload))+kind+payload+struct.pack('!I',zlib.crc32(kind+payload)&0xffffffff)
        png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',frame.width,frame.height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')
        Path(destination).write_bytes(png)
else:raise ValueError('Unsupported codec')
