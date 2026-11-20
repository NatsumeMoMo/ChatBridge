const {test}=require('node:test'),assert=require('node:assert/strict'),{Duplex}=require('node:stream'),{YamuxStream}=require('../dist/transport');
class Wire extends Duplex{sent=[];_read(){} _write(data,encoding,cb){this.sent.push(Buffer.from(data));cb();}}
function frame(type,flags,id,length,body){const h=Buffer.alloc(12);h[1]=type;h.writeUInt16BE(flags,2);h.writeUInt32BE(id,4);h.writeUInt32BE(length,8);return body?Buffer.concat([h,body]):h;}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
test('yamux handles split frames, ping, send window exhaustion and credit restoration',async()=>{
 const wire=new Wire(),stream=new YamuxStream(wire),received=[];stream.on('data',x=>received.push(x));
 const input=frame(0,2,1,3,Buffer.from('abc'));wire.push(input.subarray(0,5));wire.push(input.subarray(5));await tick();assert.equal(Buffer.concat(received).toString(),'abc');
 wire.push(frame(2,1,0,42));await tick();assert(wire.sent.some(x=>x[1]===2&&x.readUInt16BE(2)===2&&x.readUInt32BE(8)===42));
 let done=false;stream.write(Buffer.alloc(300000,9),()=>done=true);await tick();assert.equal(done,false);
 wire.push(frame(1,0,1,100000));await tick();assert.equal(done,true);stream.destroy();
});
test('yamux rejects input exceeding the receive credit',async()=>{
 const wire=new Wire(),stream=new YamuxStream(wire);const error=new Promise(resolve=>stream.once('error',resolve));
 wire.push(frame(0,0,1,262145,Buffer.alloc(262145)));assert.match((await error).message,/FLOW_CONTROL/);assert(stream.destroyed);
});
