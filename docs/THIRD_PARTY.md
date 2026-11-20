# 第三方组件与许可证

MIT 覆盖本仓库原创的插件、网关、部署工具和兼容修改。它不改变任何外部项目的授权。仓库不包含个人运行环境、微信安装包、Hook DLL、上游源码 ZIP、FFmpeg 二进制或登录资料。

| 组件 | 固定来源 / 范围 | 许可与用途 |
| --- | --- | --- |
| 微信客户端 | Windows 4.1.10.27 x64 | 专有软件，需独立合法取得；不发布其安装包或修改后的客户端 |
| WeChat Hook | [mengyunqwq/wechat-hook](https://github.com/mengyunqwq/wechat-hook)，提交 `eb57f8e2ccef4d41f1cd4d7e28a9b110323cb00b` | 源码可公开读取，但未发现明确开源许可证；不标为 OSI 开源，不将其重新许可为 MIT，不分发其源码或产物 |
| FRP | [fatedier/frp v0.68.1](https://github.com/fatedier/frp/tree/v0.68.1) | Apache-2.0；部署时单独构建 / 取得 |
| cryptography | 46–50 范围，Windows 验证锁在 gateway/requirements.windows.lock | Apache-2.0 / BSD 许可体系；TLS 身份、AEAD 和加密入网包 |
| python-zstandard | 0.25.0 | BSD / Zstandard 组件各自许可；正文解压 |
| defusedxml | 0.7.1 | PSF；限制 XML 实体解析 |
| pilk / SILK | [foyoux/pilk](https://github.com/foyoux/pilk) 及其引入的 SILK 来源 | 保留各自授权和来源；语音解码，不捆绑二进制 |
| PyAV / FFmpeg / NumPy | [PyAV](https://github.com/PyAV-Org/PyAV)、FFmpeg、NumPy | 分别保留 BSD / LGPL / GPL 等实际组件条款；FFmpeg 取决于构建选项 |
| TypeScript / VSCE / jsdom | extension/package-lock.json | 仅开发、测试和打包依赖；VSIX 不打包 node_modules |

本仓库 native 下是对固定来源进行变换的兼容脚本；其中用于定位的上游片段仍属于原权利人。公开的自有修改不提供上游整体的再分发授权。WeChatFerry 不在当前链路中：其所需旧客户端在个人验证时被平台拒绝登录。

FRP 互通依据公开的 STCP、NewVisitorConn 和 yamux 协议；插件自带 TypeScript 访问端。FRP 的签名算法不是聊天内容加密算法，内容始终由独立 TLS 1.3 mTLS 保护。

完整运行链路因此**不是全部依赖都具备明确开源许可证的系统**。这个仓库公开的是原创链路与可重建流程，并明确保留微信客户端和 Hook 的边界。
