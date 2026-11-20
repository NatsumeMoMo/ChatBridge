# 固定微信 Hook 的独立构建

仅针对 Windows 微信 4.1.10.27 x64。上游源码公开但许可证不明确，不随本仓库发布，不宣称具有开源再分发授权。只发布原创兼容脚本，并保留上游片段的权利边界。

1. 自行取得微信 4.1.10.27。已验证核心 Weixin.dll SHA-256：`f487e4f56930456a25fb0d7b058c77e2dcd364e268d49bdfff4dc0e3be26ba36`。固定版本安装器样本 SHA-256：`54203fc2b41983fa106b0af0d67f86befc56ccd3dc1005d4bab6de8ea36b4f74`；不提供二进制镜像。校验你实际使用的启动器与核心，不把版本文本相同当成适配一致。
2. 独立下载 [固定上游提交 ZIP](https://github.com/mengyunqwq/wechat-hook/archive/eb57f8e2ccef4d41f1cd4d7e28a9b110323cb00b.zip)，SHA-256：`cca60b64e3975582eb9a2b59fb7d3e3c3a91c6347cc5fb7fa03a2928f69bcd3c`。
3. 在新解压目录执行 `python native/patch_private_queue.py PATH_TO_SOURCE`。补丁绑定专用布局：队列 `C:\ChatBridgeValidation\private\hook-queue`，日志 `C:\ChatBridgeValidation\private\upstream-logs`。安装器限制这些目录访问权限。若修改路径，需要重新构建并核对输出，不编辑正在运行的源码 / DLL。
4. 用 VS Build Tools 的 v143 C++ x64 工具集和 Windows SDK 构建 `x64_Version_dll.vcxproj`：`msbuild /t:Build /p:Configuration=Release /p:Platform=x64 /m:2 /p:MultiProcessorCompilation=false`。
5. 将自己构建的 version.dll 放到专用微信程序目录，记录其 SHA-256 到 profile；启动器、核心和 Hook 哈希都由守卫检查。编译器 / 时间戳可能导致与你的旧构建不同的二进制哈希，不能随意填旧哈希。

基线保留防撤回、诊断及内容日志，关闭原生无认证 HTTP API，改由受限本地文件队列分派。没有新增偏移扫描、原生语音发送或主动附件下载。Hook 自身可能不显示在常规模块列表中；不能据此判断它没有加载，网关会通过只读队列响应与数据库读取检查就绪。

`patch_unicode.py` 是候选兼容修改，修复 UTF-8 路径和 UTF-16 长度处理；它曾从固定源码构建，但未完成真实发送回执验收。默认部署使用基线，不自动启用该候选。当前网关和插件明确拒绝非 ASCII 附件名。

Hook 更新必须连同专用客户端核心在受控副本验证；不要直接替换日常在线环境。媒体编解码独立部署，SILK / WXGF 不依赖在本仓库内分发客户端二进制。
