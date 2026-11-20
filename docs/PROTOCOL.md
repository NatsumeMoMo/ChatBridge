# CBR1 应用协议

CBR1 是自定义应用层封包，密码学使用标准 TLS 1.3。安全性不依赖协议格式保密。B 验证网关 CA、主机名和叶证书 SHA-256 指纹；网关验证客户端证书链，并在每个请求前检查叶证书指纹白名单。TLS 1.2 禁用。

## 帧

16 字节大端头：`magic[4]='CBR1', major:u8=1, minor:u8=0, type:u8=1, flags:u8=0, payloadLength:u32, paddedLength:u32`。JSON 是 UTF-8，后接随机填充。JSON 最大 256 KiB，填充后最大 512 KiB，最小分桶 1,024 字节，其后按 2 的幂分桶。接收端在等待正文前验证版本、长度和上限；实现支持 TCP 任意切分与多帧合并。

```json
{"type":"request","id":"request-uuid","op":"messages.list","args":{"conversationId":"opaque-id"}}
```

```json
{"type":"response","id":"request-uuid","ok":true,"result":{"items":[],"nextBefore":null}}
```

失败响应包含 `error.code / message / retryable`。request id 只关联当前连接请求；业务副作用另用跨重连稳定的 32 位小写十六进制 commandId。

## 操作

| 操作 | 用途 |
| --- | --- |
| hello | 首个请求，协商版本，返回实例 ID、平台、能力、最新游标与附件块大小 |
| accounts.list | 账号与登录状态 |
| contacts.list / conversations.list | 联系人 / 最近会话，100 项分页 |
| messages.list | 最近消息及按 `(timestamp,id)` 向前翻页，避免新消息导致 offset 漏页 |
| sync.events | 从 after 游标补收修订事件；每页最多 50 事件，并受字节预算限制 |
| sync.subscribe | 在当前连接订阅 after 之后的持久事件，返回确认后主动推送；不阻塞同连接的发送和查询 |
| messages.send / commands.get | 聊天命令提交 / 结果查询 |
| attachments.begin / chunk / commit | 以 uploadKey 开始或恢复上传，32 KiB 分块，提交时校验 SHA-256 |
| attachments.read / retry | 分块读取 / 重新检查虚拟机媒体缓存 |
| modules.invoke | 已注册模块的读取或持久化写操作 |
| ping / diagnostics.get | 心跳、同步位置与脱敏诊断 |

客户端最多保留 32 个并行请求，单请求超时 25 秒。网关对每个授权设备限制连接数，对读取设置空闲超时。大附件绕过单个 JSON 上限，分块传输并在 B 重新计算完整哈希。

`hello.features.eventPush=true` 表示支持主动订阅。推送帧格式为 `{"type":"events","events":[...],"cursor":123,"latestCursor":123}`，使用同一 CBR1 / TLS 通道；事件必须来自持久账本。服务端串行写入完整帧，响应仍按 request id 对应；慢消费者触发写超时后断开，通过原游标重连恢复。设备撤销在事件发送前也重新检查。

插件读取账号与目录快照后，新设备从 hello 给出的游标接续；已有有效游标继续补收。订阅之外每 20 秒保留补查与心跳，不支持订阅的旧网关使用定时补查。推送和补查共用单调递增游标，重复事件不回退状态。

## 恢复与副作用

状态：`queued → dispatching → submitted → serverAccepted`。原生调用错误、崩溃、长时间缺回执进入 `uncertain`。重启时 dispatching 一律降为 uncertain，只查账；不能推断“没收到响应 = 没发送”。不确定状态仍可因后来发现唯一原生回执升级成功。

重复 commandId 且参数完全相同返回同一账本记录；设备或参数不同则拒绝。上传同样用稳定 uploadKey；重复块必须内容相同，错误 offset 或哈希拒绝。B 的待发队列与回执跟踪放在 SecretStorage，明文文件仅保存用户本来选择的路径；文件变化时不继续发送旧记录。

事件游标在 B 持久化，但消息历史以网关数据库为准。换网关实例或恢复到更小游标时重新同步。事件日志保留正文修订，避免撤回覆盖成为唯一记录。

## STCP 外层

现网使用 FRP 0.68.1 STCP。B 实现单流 yamux 与 NewVisitorConn / response，外层也要求 TLS 1.3，并使用从服务器可信管理通道取得的证书指纹；访客请求签名按 FRP 现有算法生成，不能把它视为聊天加密算法。内层 CBR1 / mTLS 完整穿过 yamux 与 frpc。

本项目使用独立随机 STCP secret，并由内层 mTLS 鉴权内容访问。新部署模板为 FRPS 另设随机注册 token。共享 FRPS 沿用其管理配置，STCP 不要求另开放聊天网关公网端口。

分桶填充只能降低单条请求长度的精度。固定轮询、批量同步与附件分块仍有时间和总量特征；本版未实现恒速双向 cover traffic，不能声称阻止所有流量分析。
