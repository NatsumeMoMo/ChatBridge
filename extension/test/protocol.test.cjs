const test=require('node:test');const assert=require('node:assert/strict');
const {encode,Decoder}=require('../dist/protocol');
test('fragmented, coalesced frames and Chinese strings',()=>{
 const frames=Buffer.concat([encode({a:'你好'}),encode({b:2})]);const decoder=new Decoder();let result=[];
 for(let i=0;i<frames.length;i+=7)result.push(...decoder.push(frames.subarray(i,i+7)));
 assert.deepEqual(result,[{a:'你好'},{b:2}]);
});
test('hostile lengths and incompatible versions rejected before body allocation',()=>{
 for(const mutate of [x=>x.writeUInt32BE(0xffffffff,12),x=>x[4]=2,x=>x.writeUInt32BE(0,8)]){
   const frame=encode({a:1});mutate(frame);assert.throws(()=>new Decoder().push(frame.subarray(0,16)));
 }
});
