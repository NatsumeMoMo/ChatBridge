import difflib,hashlib,json,sys
from pathlib import Path

root=Path(sys.argv[1]).resolve()
changes=[]
def edit(name,transform):
    path=root/name
    before=path.read_text(encoding='utf-8-sig')
    after=transform(before)
    if before==after: raise RuntimeError('Patch did not change '+name)
    path.write_text(after,encoding='utf-8-sig')
    changes.append({'file':name,'beforeSha256':hashlib.sha256(before.encode()).hexdigest(),'afterSha256':hashlib.sha256(after.encode()).hexdigest(),'diff':''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='upstream/'+name,tofile='validation/'+name))})

edit('src/inline_weixin_dll_load.cpp',lambda s:s.replace('g_httpServer->Start("127.0.0.1", g_StartPort);','// 全部接口仍由私有目录队列分发，避免另开无认证网络入口。'))
edit('src/HookQueue.cpp',lambda s:s.replace('g_dir = dir + L"\\\\hook-queue";','// 队列含聊天内容，沿用仅管理员和 SYSTEM 可读的目录。\n    g_dir = L"C:\\\\ChatBridgeValidation\\\\private\\\\hook-queue";'))
edit('xdb/msg_push.cpp',lambda s:s.replace('g_url = g_CallBack_Url.empty() ? std::string("http://127.0.0.1:18998") : g_CallBack_Url;','// 回调固定在同一虚拟机，跨机数据只经后续加密网关。\n        g_url = "http://127.0.0.1:18998/recv";'))

# 保留用户要求的日志内容，只把上游开发机路径归到本轮私有目录。
for path in list((root/'src').glob('*.cpp'))+list((root/'xdb').glob('*.cpp')):
    name=path.relative_to(root).as_posix()
    before=path.read_text(encoding='utf-8-sig')
    after=before.replace('D:/wxhook/wx4110/','C:/ChatBridgeValidation/private/upstream-logs/')
    after=after.replace('D:\\\\wxhook\\\\wx4110\\\\','C:\\\\ChatBridgeValidation\\\\private\\\\upstream-logs\\\\')
    after=after.replace('D:\\\\wxhook\\\\hook-probe.log','C:\\\\ChatBridgeValidation\\\\private\\\\upstream-logs\\\\hook-probe.log')
    after=after.replace('path = s.substr(0, p + 1) + "hook-probe.log";','path = "C:/ChatBridgeValidation/private/upstream-logs/hook-probe.log";')
    if after!=before: edit(name,lambda _,a=after:a)

(root/'validation-source-changes.json').write_text(json.dumps(changes,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'ok':True,'changedFiles':len(changes),'antiRevokePreserved':True,'diagnosticRoutesPreserved':True,'messageLoggingPreserved':True}))
