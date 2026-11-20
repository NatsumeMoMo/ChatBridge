import * as tls from 'node:tls';
import {Duplex} from 'node:stream';
import {createHash} from 'node:crypto';
import {Pairing} from './client';

// 按 FRP v0.68.1 的公开 STCP 与 yamux 协议实现单流，平台协议不依赖这个传输层。
export class YamuxStream extends Duplex{
  private buffer:Buffer=Buffer.alloc(0);private sendWindow=256*1024;private receiveWindow=256*1024;
  private pending?:{data:Buffer;offset:number;callback:(error?:Error|null)=>void};private blocked=false;
  constructor(private outer:Duplex){
    // 私有代理每条连接只有一个业务流，远端结束后不保留无法使用的半开连接。
    super({highWaterMark:64*1024,allowHalfOpen:false});
    outer.on('data',(data:Buffer)=>{try{this.consume(data);}catch(error){this.destroy(error as Error);}});
    outer.on('error',error=>this.destroy(error));outer.on('close',()=>this.destroy());
    this.packet(1,1,1,0);
  }
  private packet(type:number,flags:number,id:number,length:number,body?:Buffer):boolean{
    const header=Buffer.alloc(12);header[1]=type;header.writeUInt16BE(flags,2);header.writeUInt32BE(id,4);header.writeUInt32BE(length,8);
    return this.outer.write(body?Buffer.concat([header,body]):header);
  }
  private consume(data:Buffer){
    this.buffer=Buffer.concat([this.buffer,data]);
    while(this.buffer.length>=12){
      const b=this.buffer,type=b[1],flags=b.readUInt16BE(2),id=b.readUInt32BE(4),length=b.readUInt32BE(8);
      if(b[0]!==0||type>3||length>16*1024*1024)throw new Error('YAMUX_INVALID_FRAME');
      const size=12+(type===0?length:0);if(b.length<size)break;
      const payload=b.subarray(12,size);this.buffer=b.subarray(size);
      if(type===2){if(flags&1)this.packet(2,2,0,length);continue;}
      if(type===3){this.destroy(new Error('RELAY_CLOSED'));return;}
      if(id!==1){if(flags&1)this.packet(1,8,id,0);continue;}
      if(flags&8){this.destroy(new Error('RELAY_STREAM_RESET'));return;}
      if(type===1){this.sendWindow+=length;if(this.sendWindow>16*1024*1024)throw new Error('YAMUX_WINDOW_LIMIT');this.flush();}
      if(type===0){
        this.receiveWindow-=length;if(this.receiveWindow<0)throw new Error('YAMUX_FLOW_CONTROL');
        if(length)this.push(payload);this.credit();
      }
      if(flags&4)this.push(null);
    }
    if(this.buffer.length>16*1024*1024+12)throw new Error('YAMUX_BUFFER_LIMIT');
  }
  private credit(){const delta=256*1024-this.readableLength-this.receiveWindow;if(delta>0){this.receiveWindow+=delta;this.packet(1,0,1,delta);}}
  _read(){this.credit();}
  _write(data:Buffer,_encoding:BufferEncoding,callback:(error?:Error|null)=>void){this.pending={data,offset:0,callback};this.flush();}
  private flush(){
    if(this.blocked||!this.pending||this.destroyed)return;
    const item=this.pending;
    while(item.offset<item.data.length&&this.sendWindow>0){
      const count=Math.min(16384,this.sendWindow,item.data.length-item.offset),body=item.data.subarray(item.offset,item.offset+count);
      this.sendWindow-=count;item.offset+=count;
      if(!this.packet(0,0,1,count,body)){this.blocked=true;this.outer.once('drain',()=>{this.blocked=false;this.flush();});return;}
    }
    if(item.offset===item.data.length){this.pending=undefined;item.callback();}
  }
  _final(callback:(error?:Error|null)=>void){this.packet(1,4,1,0);callback();}
  _destroy(error:Error|null,callback:(error:Error|null)=>void){const pending=this.pending;this.pending=undefined;pending?.callback(error||new Error('STREAM_CLOSED'));this.outer.destroy();callback(error);}
}

function readExact(stream:Duplex,size:number):Promise<Buffer>{
  return new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>finish(new Error('RELAY_TIMEOUT')),10000);
    function cleanup(){clearTimeout(timer);stream.removeListener('readable',read);stream.removeListener('error',fail);stream.removeListener('close',close);}
    function finish(error?:Error,data?:Buffer){cleanup();error?reject(error):resolve(data!);}
    function fail(error:Error){finish(error);}function close(){finish(new Error('RELAY_CLOSED'));}
    function read(){const data=stream.read(size);if(data!==null)finish(undefined,data);}
    stream.on('readable',read);stream.once('error',fail);stream.once('close',close);read();
  });
}

export async function openStcp(pairing:Pairing):Promise<Duplex>{
  const config=pairing.transport!;
  if(!/^[a-f0-9]{64}$/.test(config.relayFingerprint))throw new Error('RELAY_PIN_REQUIRED');
  const outer=await new Promise<tls.TLSSocket>((resolve,reject)=>{
    // FRP 自签名证书通过 Workbench 通道预先核对，外层以固定指纹认证；内层仍执行完整 mTLS 验证。
    const socket=tls.connect({host:config.serverAddr,port:config.serverPort,servername:'frp',minVersion:'TLSv1.3',maxVersion:'TLSv1.3',rejectUnauthorized:false});
    const timer=setTimeout(()=>socket.destroy(new Error('RELAY_CONNECT_TIMEOUT')),10000);
    socket.once('error',reject);socket.once('secureConnect',()=>{
      clearTimeout(timer);
      if(createHash('sha256').update(socket.getPeerCertificate(true).raw).digest('hex')!==config.relayFingerprint){socket.destroy();reject(new Error('RELAY_CERTIFICATE_MISMATCH'));return;}
      resolve(socket);
    });socket.once('close',()=>clearTimeout(timer));
  });
  const stream=new YamuxStream(outer);stream.on('error',()=>{});
  try{
    const timestamp=Math.floor(Date.now()/1000);
    const body=Buffer.from(JSON.stringify({proxy_name:config.serviceName,sign_key:createHash('md5').update(config.secretKey+timestamp).digest('hex'),timestamp,use_encryption:false,use_compression:false}));
    const header=Buffer.alloc(9);header[0]='v'.charCodeAt(0);header.writeBigUInt64BE(BigInt(body.length),1);stream.write(Buffer.concat([header,body]));
    const response=await readExact(stream,9);const length=response.readBigUInt64BE(1);
    if(response[0]!=='3'.charCodeAt(0)||length>4096n)throw new Error('RELAY_INVALID_RESPONSE');
    const value=JSON.parse((await readExact(stream,Number(length))).toString('utf8'));
    if(value.error)throw new Error('RELAY_ACCESS_DENIED');
    return stream;
  }catch(error){stream.destroy();throw error;}
}

export interface Transport{pairing:Pairing;open?:()=>Promise<Duplex>;close:()=>void;}
export async function startTransport(pairing:Pairing,_extensionRoot:string,_privateDirectory:string,_diagnostic?:(code:string)=>void):Promise<Transport>{
  if(!pairing.transport)return {pairing,close:()=>{}};
  if(pairing.transport.type!=='frp-stcp')throw new Error('UNSUPPORTED_TRANSPORT');
  const streams=new Set<Duplex>();let closed=false;
  return {pairing,open:async()=>{const stream=await openStcp(pairing);if(closed){stream.destroy();throw new Error('TRANSPORT_CLOSED');}streams.add(stream);stream.once('close',()=>streams.delete(stream));return stream;},close:()=>{closed=true;for(const stream of streams)stream.destroy();streams.clear();}};
}
