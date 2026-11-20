# 节点替换、更新与恢复

## 保存什么

源码不是身份或聊天数据备份。至少在 GitHub 之外保存：加密的 `authority-backup.cbx`、对应口令、网关一致性备份、自己使用的组件版本和哈希清单。微信账号登录状态与客户端原生数据库属于微信自己的数据，网关备份不等于微信完整备份。

DPAPI 绑定 Windows 用户和系统环境。直接把 secrets.dpapi / pairing.dpapi 拷给新用户无法解密；跨用户恢复用加密 authority 备份重建新用户的 DPAPI 包装。

## 仅更新 B 插件

从受信任的 tag / commit 构建 VSIX，安装并重载窗口。设备身份、待发队列在 SecretStorage，网关历史不随插件安装包覆盖。升级前手工保留未提交草稿。回退时安装上一份 VSIX；不通过清除密钥或发送账本解决显示问题。

## 仅更新网关

在 VM 另一个源码 checkout 中：

```powershell
git fetch --tags
git checkout YOUR_REVIEWED_TAG_OR_COMMIT
.\deploy\windows\Update.ps1 -Root C:\ChatBridge
```

只切换应用目录，保留 private / data；不重启微信。旧代码保存在 app-previous 目录，失败时恢复。依赖锁发生变化时默认拒绝，需要核对后显式加 `-UpgradeDependencies`；失败时同时尝试恢复旧锁。恢复旧依赖失败会明确报错，应保持维护状态，不声称已恢复。

更新前网关已停止则保持停止。重启后必须等待当前 bootId 的账号采集完成，旧持久 online 不代表本次就绪。dispatching 状态恢复为 uncertain，只查账，不能另发同样内容猜测恢复。

已有 C:\ChatBridge 个人验证部署若仍使用旧任务名 / 私有守卫，Update.ps1 会依据 client-pin.json 自动识别并保留旧守卫、桌面入口和只读状态脚本；不要求重新配对或先迁移 profile。不要把 Install.ps1 当作覆盖安装器。需要切换目录布局时，在维护窗口保留 data 和身份，按新 profile 建立新部署根目录后再迁移。

## 制作一致备份

```powershell
.\deploy\windows\Backup-Gateway.ps1 -Root C:\ChatBridge -Destination D:\ChatBridgeBackups\YOUR_NEW_BACKUP_NAME
```

只停止网关，复制 data / private，记录每个文件哈希，并在 finally 恢复先前运行状态。备份包含个人内容、配置与 DPAPI 资料，必须保存在私有加密存储中。不要只复制活动 WAL 数据库的主文件。

## 更换 PC B / Windows 用户

在新 B：

```powershell
py -3.11 tools/enrollment.py restore-authority --backup X:\PrivateBackup\authority-backup.cbx --private-dir C:\ChatBridgePrivate
py -3.11 tools/enrollment.py export --private-dir C:\ChatBridgePrivate --relay-host relay.example.net --relay-fingerprint YOUR_TRUSTED_RELAY_SHA256 --new-device
```

原 CA、网关身份、storageKey 和 STCP secret 不变，新 B 获得独立客户端证书。将新的 guest-enrollment.cbx 交给既有 VM，运行：

```powershell
C:\ChatBridge\runtime\Scripts\python.exe C:\ChatBridge\app\tools\enroll_guest.py --root C:\ChatBridge --enrollment X:\Private\guest-enrollment.cbx --reconfigure
```

工具核对已有网关秘密一致后，才添加设备指纹和更新 frpc 配置；不会替换数据密钥。重新启动 frpc，B 导入新配对。旧 B 不再使用时，从 private/devices.json 移除其指纹；授权名单在请求和推送前复查，不需重启网关。保留更新后的加密 authority 备份。

## 更换服务器

在新服务器部署兼容 FRPS，取得新 TLS 指纹和全局注册 token。B 在**已有 authority 目录**重新 export，修改 relay-host / port / fingerprint / token-file。不要重新生成聊天 CA 或数据密钥。

VM 用上述 --reconfigure 导入并重启 `ChatBridge-RelayClient`，B 导入重新导出的 pairing。服务器证书变更必须通过可信管理通道重新核对，不能关闭指纹检查。若仅更新 FRPS 程序，备份旧二进制，保留 /etc/chatbridge-relay 下的证书与配置，先执行新版本 verify 再重启专用服务；失败恢复旧程序。共享 FRPS 的更新仍按该服务器自己的维护流程。

## 更换 VM / VM 用户

准备固定客户端、Hook 和编解码器，按新 profile 安装到新根目录；入网包来自原 authority，因此 storageKey 保持一致。停止新网关，将旧备份中完整 data 目录放回新根目录，保留新用户刚生成的 private/secrets.dpapi，不复制旧用户的 DPAPI 包装。

新的配置使用新账号路径、Python 路径和组件哈希。不要同时运行新旧 VM 处理相同账号和同一待发账本。首次启动检查账号在线、历史可读、uncertain 命令数量；新 B 已保存的实例游标若超出恢复后的网关游标，会重新同步。微信登录过期仍需手机认证。

## 更换微信 / Hook / 编解码器

它们是版本适配边界，不是任意 DLL 可热替换的组件。先在可回退的专用副本中构建、更新自己的哈希清单、验证登录及锁屏收发。只有核心、Hook、媒体兼容后才在维护窗口停止对应任务 / 客户端并切换，旧程序目录保留。

网关、frpc 和插件不需要因 Hook 文件更新而重建密钥或清空历史。保留防撤回、诊断和内容日志；不把候选 Unicode 文件名修复视为已经通过真实发送验收。证书叶默认 365 天、CA 730 天，当前没有自动续期，需要在到期前安排签发、分发和指纹更新。
