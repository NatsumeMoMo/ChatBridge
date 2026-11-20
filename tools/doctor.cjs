const path=require('node:path'),{execFileSync}=require('node:child_process');
const {Client}=require('../extension/dist/client'),{startTransport}=require('../extension/dist/transport');
async function main(){
 const pairingFile=process.argv[2];if(!pairingFile)throw Error('Usage: node tools/doctor.cjs PATH_TO_PAIRING_DPAPI [PYTHON_EXE]');
 const root=path.resolve(__dirname,'..'),python=process.argv[3]||'python';
 // 配对只在两个端点进程内解包，诊断输出不包含证书、令牌和消息正文。
 const pairing=JSON.parse(execFileSync(python,[path.join(__dirname,'read_pairing.py'),pairingFile],{encoding:'utf8',windowsHide:true}));
 const transport=await startTransport(pairing,path.join(root,'extension'),'');const client=new Client(transport.pairing,transport.open);
 try{
  const ready=new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('CONNECT_TIMEOUT')),30000);client.once('ready',hello=>{clearTimeout(timer);resolve(hello);});});
  client.start();const hello=await ready;const result=await client.request('diagnostics.get');
  console.log(JSON.stringify({protocol:hello.protocolVersion,gatewayVersion:hello.gatewayVersion,platforms:hello.platforms.map(p=>p.id),errors:result.errors,accountStatuses:result.accounts.map(a=>({platformId:a.platformId,status:a.status})),pending:result.pendingCommands,uncertain:result.uncertainCommands,adapterPolling:result.adapterPolling},null,2));
  if(result.accounts.some(a=>a.status!=='online')||Object.keys(result.errors).length)process.exitCode=1;
 }finally{client.stop();transport.close();}
}
main().catch(error=>{console.error(error.code||error.message);process.exitCode=1;});
