# 安装、备份与更新

## 官方 CLI 与源码包

先从 [Antigravity 官方文档](https://antigravity.google/docs/install?tab=cli) 安装 CLI 并完成登录。它与 Codex 登录独立，登录状态由官方工具管理，本项目不包含账号或认证文件。

macOS/Linux 包是源码预览，需要 Python 3.9+。完整解压到自己的可写目录，Mac 双击 `启动控制台.command`，Linux 从终端运行 `./Start-Agy.sh`。默认查找 `~/.local/bin/agy`，自定义路径可用 `--agy-bin /path/to/agy`。程序更新要经过 `update_bootstrap.py` 启动，直接运行 server.py 不会应用暂存更新。

保留原启动终端。关闭终端或 Ctrl+C 结束服务和自有 CLI，不关闭其他窗口的任务。默认 `127.0.0.1:8765`，端口已占用时拒绝启动。Mac 未提供 Developer ID 签名或 Apple 公证；安全打开只参考 [Apple 官方说明](https://support.apple.com/102445)。不删除检疫标记或关闭 Gatekeeper。

## Windows 便携包

目标为 Windows 10 1809+/Windows 11 x64。完整解压到可写目录，避免 Program Files 和 ZIP 内直接运行。包内含 Python、pywinpty、psutil 和各自许可，无需安装 Python。

官方 CLI 安装登录后双击 `Start-Agy.cmd`。需要登录时用 `Login-Agy.cmd`；启动前可设 `AGY_BINARY` 为自定义 agy.exe 路径。`Diagnose.cmd` 只读检查依赖/目录/端口；`Self-Test.cmd` 检验管道、ConPTY、Job 监管的测试子进程清理，不调用模型。

Windows 原生检查和真实 CLI 调用分开记录；正式发布前以验证记录为准，不把跨平台编译或 Mac 便携通用测试视为 Windows 实机通过。

## 配置迁移与聊天备份

设置 → 导出配置 JSON；在另一端导入并预览。窗口模型/候补/协作开关默认不应用，用户明确选择目标窗口后才应用。导入前保留原配置，校验模型可用性与预览是否过期；不导入聊天、固定绑定、邮箱或凭据。

聊天、图片和固定绑定使用独立的面板备份。备份含敏感历史和可能的邮箱，不上传 Issue/Release。面板备份不恢复官方 CLI 的底层上下文或登录，跨电脑恢复界面记录不能保证续接原官方会话。

## 更新与卸载

只支持公开 GitHub Releases。自动下载默认关闭；即使关闭自动下载，配置发布源后仍会按日限检查。空闲时下载并校验 ZIP 和逐文件摘要，下次前台启动应用；不在运行中替换程序。失败或未确认启动会回滚。官方账号、data、Python 运行时与项目文件保留。

`*-program.zip` 不是首次安装包；其中不含稳定 bootstrap。协议、运行时或 bootstrap 升级需要新的完整包。已有未知旧安装须先核对结构并备份，不直接把新包覆盖个人数据。源码版手动更新先关终端，保留 data，按发布说明替换程序。

卸载前关闭服务并备份数据；删除本项目程序不应删除官方 CLI、登录、其他聊天或项目。没有新增开机启动、后台服务或付费任务。
