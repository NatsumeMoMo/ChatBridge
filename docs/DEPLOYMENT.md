# 完整部署步骤

## 1. 准备与边界

部署节点是 Windows VM、Linux 中继服务器、Windows PC B。VM 与宿主分离，宿主只负责运行虚拟机；WorkLink、VMware 或云厂商管理工具不是运行依赖。部署可通过自己已有的 SSH、RDP、虚拟机控制台或可信文件传输完成。

VM 用户需有安装软件、创建计划任务和限制目录权限的能力。安装后任务使用该用户的交互登录会话，不保存 Windows 密码。所有命令从仓库 checkout 执行；本机路径、账号根目录和哈希由自己填写。

先完成固定 Windows 微信 4.1.10.27 x64 的登录与本地 Hook 构建，参见 [native/README.md](../native/README.md)。不要在正常使用的宿主微信上覆盖 DLL。默认补丁队列位于 `C:\ChatBridgeValidation\private\hook-queue`，内容日志位于同级 `upstream-logs`；安装器会限制该私有目录权限。

## 2. 构建 / 获取 FRP 0.68.1

Windows 可以执行：

```powershell
.\tools\build-frp.ps1 -OutputDirectory C:\ChatBridgeBuild\frp-0.68.1
```

需要 Git、Go，Go 版本按 FRP 固定提交的 go.mod。Linux 在新目录执行：

```bash
git clone --depth 1 --branch v0.68.1 https://github.com/fatedier/frp.git frp-source
cd frp-source
go build -trimpath -o ../frps ./cmd/frps
sha256sum ../frps
git rev-parse HEAD
```

也可自行核对官方发行包。部署安装脚本要求明确提供二进制 SHA-256，不下载未校验程序。Windows 与 Linux 产物分别构建，不能互换。

## 3. Linux 服务器

服务器只需 FRPS；生成的材料只有外层 TLS 证书、注册 token，没有聊天 CA、端点私钥或数据密钥。以下命令在服务器 checkout 中执行：

```bash
python3 -m venv /tmp/chatbridge-relay-tools
/tmp/chatbridge-relay-tools/bin/pip install -r tools/requirements.txt
/tmp/chatbridge-relay-tools/bin/python deploy/server/prepare.py --output-dir /root/chatbridge-relay-private --port 7000
sudo bash deploy/server/install.sh /root/frps /root/chatbridge-relay-private YOUR_FRPS_SHA256
```

`prepare.py` 拒绝覆盖非空目录；`install.sh` 拒绝覆盖已有独立 ChatBridge 中继配置。云防火墙和主机防火墙允许 B / VM 访问所选 TCP 控制端口。STCP 不需要另开放聊天网关端口。服务器已有共享 FRPS 时，复用既有服务，不运行会安装独立服务的脚本；从可信管理通道取得其证书指纹、注册 token 和版本信息。

通过可信通道将服务器的 `relay-public.json`（公开指纹）与 `frp-token.txt`（秘密）传给 B；不要通过未核验的网络连接现取指纹后自动信任。

## 4. B 端生成身份和加密入网包

在 B 安装 `tools/requirements.txt` 的依赖，然后执行：

```powershell
py -3.11 tools/enrollment.py export --private-dir C:\ChatBridgePrivate --relay-host relay.example.net --relay-port 7000 --relay-fingerprint YOUR_TRUSTED_RELAY_SHA256 --frp-token-file C:\ChatBridgePrivate\frp-token.txt
```

用交互提示输入至少 12 字符的传输 / 备份口令，不把口令写在命令行。程序生成：

- `authority.dpapi`：绑定 B 当前用户的管理身份，包含 CA、端点和数据密钥。
- `pairing.dpapi`：供 B 扩展导入的副本；导入后扩展会消费该副本，管理身份仍保留。
- `guest-enrollment.cbx`：加密的 VM 入网包，只有网关身份、数据密钥、设备白名单与 frpc 配置材料，不含 B 私钥和管理 CA 私钥。
- `authority-backup.cbx`：可跨 Windows 用户恢复的加密管理备份。

重复 export 复用已有 CA、服务端和数据密钥；不要为了换服务器删除 authority。更换 B 并需独立设备身份时加 `--new-device`，随后在网关添加新指纹并按需撤销旧指纹。

把 `guest-enrollment.cbx` 通过可信文件传输交给 VM，并在 VM 交互输入口令。服务器不参与解密或保存入网资料。加密备份及口令另行保管，不提交 Git。

## 5. Windows VM 媒体环境与配置

准备 Python 3.10+ 和自构建的 Hook。媒体环境可独立于网关：

```powershell
C:\Python310\python.exe -m venv C:\ChatBridgeCodecs
C:\ChatBridgeCodecs\Scripts\python.exe -m pip install pilk av numpy
```

pilk 可使用其公开源码自行构建；需要 C/C++ 构建工具。PyAV / FFmpeg 构建的许可取决于实际启用组件。把自己验证过的环境锁定到本地版本清单，不把不同编解码器版本直接宣称为已验收；仓库不捆绑这些二进制。

复制 `deploy/windows/profile.example.json` 为仓库外的 `profile.json`，填写：

- 安装根目录与 Python、frpc 路径及 frpc 哈希。
- 固定微信启动器、实际核心 DLL、Hook 文件与对应哈希。Hook 哈希使用你自己编译的输出。
- 专用微信账号根目录、自己的原生账号 ID、Hook 队列、语音与图像环境 Python。

`accountRoot` 是已登录账号的数据目录，而不是微信程序目录；`ownId` 用于群消息方向识别。不要把填写过的 profile 上传到 issue。

在 VM 以最终运行用户、管理员 PowerShell 执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\Install.ps1 -ProfilePath C:\ChatBridgePrivate\profile.json -EnrollmentPath C:\ChatBridgePrivate\guest-enrollment.cbx
```

安装器验证所有文件哈希、创建隔离运行环境、在 VM 用户下使用 DPAPI 包装密钥，并注册 `ChatBridge-Client / ChatBridge-Gateway / ChatBridge-RelayClient`。网关仅绑定 `127.0.0.1:19443`。若目标已有 secrets.dpapi，安装器拒绝覆盖，转用更新流程。

登录 Windows 桌面后，双击 **“启动 ChatBridge”** 快捷方式即可。深色启动窗口分别显示微信、消息服务和连接通道；显示“已就绪”后即可锁屏虚拟机，在 PC B 连接。关闭启动窗口不会关闭已经启动的后台组件。

首次微信可能需要手机扫码或确认登录，窗口会等待认证后自动继续。客户端已进入聊天主界面、数据库尚不可读时显示“正在初始化消息接口”；不会把接口初始化失败提示成需要重新扫码。Windows 重启后仍需该用户先登录，启动器不保存 Windows 密码、不配置自动登录。

启动器只启动缺失的组件，重复双击会回到已有窗口。健康检查直接核对当前进程、监听端口、通道连接和本轮网关采集结果，避免旧状态或慢 WMI 查询误判。失败可点“重新检查”或“查看日志”；日志位于安装目录的 `private\launcher.log`，状态为 `private\launcher-status.json`。不会通过反复重启微信处理接口错误。

已有安装可单独更新桌面入口，无需重新配对或替换聊天历史：

```powershell
.\deploy\windows\Install-Launcher.ps1 -Root C:\ChatBridge -NoLaunch
```

`Install.ps1` 和 `Update.ps1` 都会安装该入口，旧 CMD 文件移到私有备份目录。原有 `Start.ps1` / `Start-ChatBridge.ps1` 路径继续转到同一启动器；自动化需要无窗口检查时可使用 `Start.ps1 -Headless -WaitSeconds 900`。启动时完成客户端固定哈希检查，只有联系人、会话和消息库完成初始化且实际采集成功后才显示就绪。

## 6. PC B 插件

构建 VSIX 后安装：

```powershell
cursor --install-extension .\extension\chatbridge-0.2.1.vsix --force
# VS Code 使用 code --install-extension 同一个文件
```

打开 ChatBridge，左下角设置 → 设备配对，选择本机 `pairing.dpapi`。私钥由编辑器 SecretStorage 保存。安装更新后重载窗口前保留尚未提交的草稿；已保存待发项不会因重载丢失。

如需只读诊断，先再次 export 得到配对副本，然后运行：

```powershell
node tools/doctor.cjs C:\ChatBridgePrivate\pairing.dpapi C:\Python311\python.exe
```

它输出账号状态、错误码、轮询耗时和待发数量，不向联系人发送消息。工具与插件都使用相同 CBR1 / mTLS / STCP 路径。
