import {randomBytes} from 'node:crypto';
export const MAX_PAYLOAD=256*1024;
export const MAX_PADDED=512*1024;
export function encode(value:unknown):Buffer{
  const data=Buffer.from(JSON.stringify(value),'utf8');
  if(!data.length||data.length>MAX_PAYLOAD)throw new Error('FRAME_TOO_LARGE');
  const padded=Math.max(1024,2**Math.ceil(Math.log2(data.length)));
  const header=Buffer.alloc(16);header.write('CBR1');header[4]=1;header[6]=1;
  header.writeUInt32BE(data.length,8);header.writeUInt32BE(padded,12);
  return Buffer.concat([header,data,randomBytes(padded-data.length)]);
}
export class Decoder{
  private buffer:Buffer=Buffer.alloc(0);
  push(chunk:Buffer):any[]{
    this.buffer=Buffer.concat([this.buffer,chunk]);const result=[];
    while(this.buffer.length>=16){
      const h=this.buffer;
      if(h.toString('ascii',0,4)!=='CBR1'||h[4]!==1||h[5]!==0||h[6]!==1||h[7]!==0)throw new Error('UNSUPPORTED_FRAME');
      const length=h.readUInt32BE(8),padded=h.readUInt32BE(12);
      if(!length||length>MAX_PAYLOAD||padded<length||padded>MAX_PADDED)throw new Error('INVALID_LENGTH');
      if(h.length<16+padded)break;
      const value=JSON.parse(h.toString('utf8',16,16+length));
      if(!value||Array.isArray(value)||typeof value!=='object')throw new Error('INVALID_ENVELOPE');
      result.push(value);this.buffer=h.subarray(16+padded);
    }
    if(this.buffer.length>MAX_PADDED+16)throw new Error('BUFFER_LIMIT');
    return result;
  }
}
