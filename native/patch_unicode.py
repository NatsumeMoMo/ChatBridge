import hashlib,json,sys
from pathlib import Path

root=Path(sys.argv[1]).resolve()
path=root/'src'/'tools.cpp';text=path.read_text(encoding='utf-8-sig')
for operation in ('exists','is_regular_file','file_size'):
    text=text.replace('fs::'+operation+'(filePath)','fs::'+operation+'(fs::path(reinterpret_cast<const char8_t*>(filePath.c_str())))')
if '// ChatBridge Unicode path' not in text:
    text=text.replace('uint64_t 文件_取大小(const std::string& filePath) {','// ChatBridge Unicode path：队列传入 UTF-8，不能再按 Windows 系统代码页构造路径。\nuint64_t 文件_取大小(const std::string& filePath) {')
path.write_text(text,encoding='utf-8-sig')
send=root/'src'/'wx_send.cpp';text=send.read_text(encoding='utf-8-sig')
text=text.replace('        *(uint64_t*)(msgBuf + 0x100 / 8) = imgPath.size(); // 图片路径长度','        // Unicode 辅助函数已按 UTF-16 写好长度，不能用 UTF-8 字节数覆盖。')
text=text.replace('        *(uint64_t*)(msgBuf + 0x108 / 8) = CalcWxCapacity(imgPath.size());','')
send.write_text(text,encoding='utf-8-sig')
expected={'src/tools.cpp':'bd6bf2f5bb8210efe4b191f1cc6fe0a698ce97e79cee0628a9ea9a45295784cb',
          'src/wx_send.cpp':'60cef254c70e7e7eeef6e931d11a4cedf0a2068a68182553c50d92cf3fabf36e'}
actual={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in expected}
if actual!=expected:raise RuntimeError('Source differs from the validated revision: '+json.dumps(actual))
print(json.dumps({'ok':True,'files':actual}))
