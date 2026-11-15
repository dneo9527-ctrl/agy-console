# Agy Console v0.1.0-preview.3 发布方案

目标仓库：`dneo9527-ctrl/agy-console`。所有者已明确授权公开＋MIT。

标签 `v0.1.0-preview.3`，标记 prerelease，不设为稳定最新版。

交付：Mac/Linux 需 Python 的源码预览包、Windows x64 含运行时便携预览包、五个平台程序更新 ZIP、聚合 updates.json 和 SHA256SUMS。完整包内置明确的公开仓库与 preview 通道，自动下载默认关闭。

冻结开发输入的逐文件摘要保存在 developer-source.json。发布副本使用自绘 SVG、补全说明和依赖哈希，不复制现用服务或个人状态。附件按发布副本重建，源码、程序及发布配置可追溯。

验收依据：235项Python、30组JavaScript、7项框架行为与5平台客户端校验；正式远端检查与公开下载结果见本版本Release说明。Windows/Linux真实账号登录与模型调用、旧macOS及新Mac隔离首开、Apple公证未验收。
