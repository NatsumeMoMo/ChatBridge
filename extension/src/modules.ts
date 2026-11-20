import * as vscode from 'vscode';

export interface ModuleContext{
  platformId:string;accountId:string;moduleId:string;
  invoke(operation:string,params?:Record<string,unknown>,commandId?:string):Promise<unknown>;
}
export interface FrontendModule{id:string;open(context:ModuleContext):void|Promise<void>;}
export class Modules{
  private values=new Map<string,FrontendModule>();
  register(module:FrontendModule):vscode.Disposable{
    if(!/^[a-z][a-z0-9.-]{0,63}$/.test(module.id)||module.id==='messaging'||this.values.has(module.id))throw new Error('模块标识无效或重复');
    this.values.set(module.id,module);
    return {dispose:()=>{if(this.values.get(module.id)===module)this.values.delete(module.id);}};
  }
  has(id:string){return this.values.has(id);}
  async open(context:ModuleContext){const module=this.values.get(context.moduleId);if(!module)throw new Error('尚未安装该功能的界面模块');await module.open(context);}
}
