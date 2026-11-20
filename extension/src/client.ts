import * as tls from 'node:tls';
import {randomUUID,createHash} from 'node:crypto';
import {EventEmitter} from 'node:events';
import {Decoder,encode} from './protocol';
import {Duplex} from 'node:stream';
export interface Pairing{host:string;port:number;serverName:string;caPem:string;clientCertPem:string;clientKeyPem:string;serverFingerprint:string;transport?:{type:string;serverAddr:string;serverPort:number;authToken?:string;secretKey:string;serviceName:string;relayFingerprint:string};}
export class RemoteError extends Error{constructor(public code:string,message:string,public retryable=false){super(message);}}
export class Client extends EventEmitter{
  private socket?:tls.TLSSocket;private generation=0;private stopping=false;private timer?:NodeJS.Timeout;
  private attempt=0;private requests=new Map<string,{resolve:(v:any)=>void;reject:(e:Error)=>void;timer:NodeJS.Timeout}>();
  public connected=false;public hello:any;
  constructor(private pairing:Pairing,private dial?:()=>Promise<Duplex>){super();}
  start(){this.stopping=false;this.connect();}
  stop(){this.stopping=true;this.generation++;clearTimeout(this.timer);this.connected=false;this.socket?.destroy();this.rejectAll();}
  reconnect(){this.stop();this.attempt=0;this.start();}
  private rejectAll(){for(const request of this.requests.values()){clearTimeout(request.timer);request.reject(new RemoteError('CONNECTION_LOST','连接中断，发送结果将按原编号核对',true));}this.requests.clear();}
  private async connect(){
    if(this.stopping)return;const generation=++this.generation;this.emit('state','connecting');
    const decoder=new Decoder();
    let underlying:Duplex|undefined;
    try{underlying=this.dial?await this.dial():undefined;}catch(error:any){
      if(generation!==this.generation)return;this.emit('diagnostic',error.code||error.message||'TRANSPORT_FAILED');this.schedule(generation);return;
    }
    if(generation!==this.generation){underlying?.destroy();return;}
    const socket=tls.connect({...(underlying?{socket:underlying}:{host:this.pairing.host,port:this.pairing.port}),servername:this.pairing.serverName,ca:this.pairing.caPem,
      cert:this.pairing.clientCertPem,key:this.pairing.clientKeyPem,minVersion:'TLSv1.3',maxVersion:'TLSv1.3',rejectUnauthorized:true});
    this.socket=socket;const handshake=setTimeout(()=>socket.destroy(new Error('HANDSHAKE_TIMEOUT')),12000);
    socket.on('secureConnect',async()=>{
      clearTimeout(handshake);
      const cert=socket.getPeerCertificate(true);const fingerprint=createHash('sha256').update(cert.raw).digest('hex');
      if(!socket.authorized||fingerprint!==this.pairing.serverFingerprint||socket.getProtocol()!=='TLSv1.3'){socket.destroy(new Error('CERTIFICATE_MISMATCH'));return;}
      this.connected=true;
      try{this.hello=await this.request('hello',{version:1,client:'vscode',clientVersion:'0.2.1'});if(generation!==this.generation)return;
        this.attempt=0;this.emit('state','online');this.emit('ready',this.hello);
      }catch{socket.destroy();}
    });
    socket.on('data',(chunk:Buffer)=>{
      if(generation!==this.generation)return;
      try{for(const message of decoder.push(chunk)){
        if(message.type==='events'){
          if(!Array.isArray(message.events)||!Number.isSafeInteger(message.cursor)||!Number.isSafeInteger(message.latestCursor)||message.cursor>message.latestCursor)throw new Error('INVALID_EVENTS');
          this.emit('events',message);continue;
        }
        if(message.type!=='response'||typeof message.id!=='string')throw new Error('INVALID_RESPONSE');
        const request=this.requests.get(message.id);if(!request)continue;
        this.requests.delete(message.id);clearTimeout(request.timer);
        if(message.ok)request.resolve(message.result);else request.reject(new RemoteError(message.error?.code||'REMOTE_ERROR',message.error?.message||'请求失败',!!message.error?.retryable));
      }}catch{socket.destroy(new Error('PROTOCOL_ERROR'));}
    });
    socket.on('error',(error:any)=>this.emit('diagnostic',error.code||error.message||'NETWORK_ERROR'));
    socket.on('close',()=>{
      clearTimeout(handshake);if(generation!==this.generation)return;
      this.schedule(generation);
    });
  }
  private schedule(generation:number){
    if(generation!==this.generation)return;this.connected=false;this.rejectAll();this.emit('state','offline');
    if(!this.stopping){const delay=Math.min(30000,1000*2**Math.min(this.attempt++,5))*(.8+Math.random()*.4);this.timer=setTimeout(()=>this.connect(),delay);}
  }
  request(op:string,args:any={}):Promise<any>{
    if(!this.connected||!this.socket)return Promise.reject(new RemoteError('OFFLINE','尚未连接到网关',true));
    if(this.requests.size>=32)return Promise.reject(new RemoteError('BACKPRESSURE','请求较多，请稍后再试',true));
    const id=randomUUID();let frame:Buffer;
    try{frame=encode({type:'request',id,op,args});}catch(error){return Promise.reject(error);}
    return new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>{this.requests.delete(id);reject(new RemoteError('REQUEST_TIMEOUT','请求超时，发送将按原编号核对',true));},25000);
      this.requests.set(id,{resolve,reject,timer});
      this.socket!.write(frame,(error)=>{if(error){clearTimeout(timer);this.requests.delete(id);reject(error);}});
    });
  }
}
