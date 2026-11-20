> **升级门槛：preview.16 及更早 → preview.23 必须先使用完整包。** 旧稳定 bootstrap 不接受新模块 `transcript_export.py`，且普通程序包不会更新 bootstrap。关闭原工具、保留数据后完整升级；本版不发布 program ZIP 或 updates feed。

# 安装、备份与更新

## preview.16 及更早升级到 preview.23

本版新增运行模块，必须使用完整安装／源码包；旧 .16 及更早的 program-only 更新白名单无法接受本次模块。默认不提供 program.zip 或自动更新清单，不能用旧更新器代替完整升级。

Windows：先关闭原前台终端及其子任务，再运行本版完整安装向导，使用原有每用户安装位置；不要先卸载。安装／卸载默认保留数据，便携数据与安装版数据不自动合并。

Mac/Linux：先按原面板备份功能导出历史，关闭前台终端；把本版完整源码包解压到可写的新目录。要沿用数据时，在旧工具已退出后自行备份并复制旧 data 到新目录的 data，再使用新目录的启动入口。不要复制旧程序文件来覆盖新增模块，也不要删除数据来强装。官方 CLI 登录由官方客户端继续管理，本包不携带登录。

配套 Skill 中保存的连接路径不会随目录自动改变；显式重新连接 agy-console，并按 [Skill 说明](CODEX-SKILL.md) 安装随附 external-ai-team preview.6 九文件版。安装外部 Skill 会拒绝覆盖已有同名目标，不修改审批或提供方偏好。

Mac/Linux 仍需 Python，Windows 完整包内置 Python；签名、公证、真实登录和跨平台硬件验收范围分别记录。实际版本与结果以最终 Release 为准。


## 官方 CLI 与源码包

先从 [Antigravity 官方文档](https://antigravity.google/docs/install?tab=cli) 安装 CLI 并完成登录。它与 Codex 登录独立，登录状态由官方工具管理，本项目不包含账号或认证文件。

macOS/Linux 包是源码预览，需要 Python 3.9+。完整解压到自己的可写目录，Mac 双击 `启动控制台.command`，Linux 从终端运行 `./Start-Agy.sh`。默认查找 `~/.local/bin/agy`，自定义路径可用 `--agy-bin /path/to/agy`。程序更新要经过 `update_bootstrap.py` 启动，直接运行 server.py 不会应用暂存更新。

保留原启动终端。关闭终端或 Ctrl+C 结束服务和自有 CLI，不关闭其他窗口的任务。默认 `127.0.0.1:8765`，端口已占用时拒绝启动。Mac 未提供 Developer ID 签名或 Apple 公证；安全打开只参考 [Apple 官方说明](https://support.apple.com/102445)。不删除检疫标记或关闭 Gatekeeper。

## Windows 安装版与便携包

本版提供每用户免管理员 setup.exe，创建桌面/开始菜单与首次使用指南；官方 CLI 仍需本人安装登录。安装版 data 独立于程序目录，升级/卸载保留；运行中的本工具会阻止维护，不会被安装器强杀。未知便携目录不能直接覆盖为安装版。具体入口、路径和边界见 [Windows 说明](../windows/README.md)。

目标为 Windows 10 1809+/Windows 11 x64。完整解压到可写目录，避免 Program Files 和 ZIP 内直接运行。包内含 Python、pywinpty、psutil 和各自许可，无需安装 Python。

官方 CLI 安装登录后双击 `Start-Agy.cmd`。需要登录时用 `Login-Agy.cmd`；启动前可设 `AGY_BINARY` 为自定义 agy.exe 路径。`Diagnose.cmd` 只读检查依赖/目录/端口；`Self-Test.cmd` 检验管道、ConPTY、Job 监管的测试子进程清理，不调用模型。

Windows 原生检查和真实 CLI 调用分开记录；正式发布前以验证记录为准，不把跨平台编译或 Mac 便携通用测试视为 Windows 实机通过。

## 配置迁移与聊天备份

设置 → 导出配置 JSON；在另一端导入并预览。窗口模型/候补/协作开关默认不应用，用户明确选择目标窗口后才应用。导入前保留原配置，校验模型可用性与预览是否过期；不导入聊天、固定绑定、邮箱或凭据。

聊天、图片和固定绑定使用独立的面板备份。备份含敏感历史和可能的邮箱，不上传 Issue/Release。面板备份不恢复官方 CLI 的底层上下文或登录，跨电脑恢复界面记录不能保证续接原官方会话。

## 更新与卸载

只支持公开 GitHub Releases。自动下载默认关闭；即使关闭自动下载，配置发布源后仍会按日限检查。空闲时下载并校验 ZIP 和逐文件摘要，下次前台启动应用；不在运行中替换程序。失败或未确认启动会回滚。官方账号、data、Python 运行时与项目文件保留。

本版只提供完整包，不提供 `*-program.zip` 或 updates feed。上述程序更新机制保留用于支持相同模块与 bootstrap 的版本；它不能代替本次完整升级。已有未知旧安装须先核对结构并备份，不直接把新包覆盖个人数据。源码版手动更新先关终端，备份并保留 data，按本页的完整升级步骤迁移。

卸载前关闭服务并备份数据；删除本项目程序不应删除官方 CLI、登录、其他聊天或项目。没有新增开机启动、后台服务或付费任务。
