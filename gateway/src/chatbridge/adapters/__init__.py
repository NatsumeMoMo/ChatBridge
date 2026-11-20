from typing import Protocol,Any
from importlib.metadata import entry_points
import re

class Adapter(Protocol):
    manifest:dict[str,Any]
    account_id:str
    async def poll(self):...
    async def send(self,command):...
    async def reconcile(self,command):...
    async def media(self,message):...

def create_adapter(store,entry,common):
    name=entry['type']
    if name=='wechat':
        from .wechat import WeChat
        factory=WeChat
    else:
        # 外部平台通过安装的包注册，配置文件不能任意导入或执行代码。
        matches=list(entry_points(group='chatbridge.adapters',name=name))
        if len(matches)!=1:raise ValueError('Adapter entry point must be unique: '+name)
        factory=matches[0].load()
    adapter=factory(store,{**entry,**common})
    manifest=adapter.manifest
    if not re.fullmatch(r'[a-z][a-z0-9.-]{0,63}',manifest.get('id','')):raise ValueError('Invalid platform ID')
    if not isinstance(manifest.get('modules'),list) or not isinstance(manifest.get('capabilities'),dict):raise ValueError('Invalid adapter manifest')
    if not isinstance(adapter.account_id,str) or not adapter.account_id.startswith(manifest['id']+':'):raise ValueError('Account must be namespaced by platform')
    return adapter
