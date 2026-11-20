> **preview.24 及更早须使用完整 setup 或完整便携包升级到 preview.25；保留原 data，先关闭工具。禁止使用 program-only ZIP 或更新 feed 代替。**

# Agy 控制台 · Windows x64

Windows 安装版版本：0.1.0-preview.25（2026-11-20）。Windows 10 1809+/11 x64 目标；含 Python 3.13.17、pywinpty、psutil，无需另外安装 Python。每用户免管理员安装，不创建后台服务或开机启动。本版不提供 32 位或 ARM64 原生运行时。

## 下载与首次使用

- `AgyConsole-0.1.0-preview.25-windows-x64-setup.exe`：普通用户的安装向导；桌面和开始菜单入口，默认安装在 `%LOCALAPPDATA%\Programs\AgyConsole`。
- `AgyConsole-0.1.0-preview.25-windows-x64-preview.zip`：完整便携版；完全解压到自己的可写目录，不从 ZIP 内运行。
- 本版保留公开 `.24` 的原生启动器和安装器布局，只提供完整安装/便携包和可重建源码；共享更新清单不提供 Windows 程序更新条目。已有便携版需先关闭，再下载完整包；不要用旧程序更新机制代替安装器升级。

安装后阅读 **Getting Started**，开始菜单提供 **Official CLI Installation**、**Official CLI Login**、**Agy Console**、**Open Panel**。便携目录也有对应的 `Install-Official-CLI.url`、`Login-Agy.cmd`、`Start-Agy.cmd`、`Open-Panel.url`。

1. 先通过 [官方页面](https://antigravity.google/download#antigravity-cli) 安装 Antigravity CLI；本包不自动执行远程安装脚本，不重新分发官方 CLI。
2. 双击 **Official CLI Login / Login-Agy.cmd**，在官方界面本人登录一次。已有官方登录可跳过；不含发布者 Mac 登录，不需每个聊天重新登录。
3. 双击桌面 **Agy Console** 或 **Start-Agy.cmd**，保持启动窗口打开。服务就绪后使用 **Open Panel / Open-Panel.url** 打开 <http://127.0.0.1:8765/>。
4. 关闭启动窗口或按 Ctrl+C，停止本工具拥有的服务和 CLI 子进程；仅关闭网页不会停止服务。端口占用时拒绝启动，不接管或停止已有服务。

官方 CLI 默认位置 `%LOCALAPPDATA%\agy\bin\agy.exe`，也支持 PATH；自定义位置可设 `AGY_BINARY` 为完整 exe 路径，不能填入密钥。

## 数据、升级和卸载

安装版数据在 `%LOCALAPPDATA%\AgyConsole\data`，与程序目录分离；便携版继续使用解压目录里的 `data`。可显式传 `--data "D:\My Data\Agy"`，不会自动搬运或合并未知旧数据。

安装版升级先结束任务并关闭本工具的启动/登录窗口，再运行新的完整 setup.exe，选择原程序目录。安装与卸载检查本工具在当前 Windows 登录会话中的专用 Mutex；运行中会阻止并要求用户先关闭，不自动结束任务，不按进程名强杀其他 Python/CLI。不要在另一个 Windows 登录会话中同时维护同一安装。未知便携目录不可直接覆盖为安装版，须选独立程序目录并自行保留旧数据。

在 Windows“已安装的应用”或开始菜单卸载本工具，只移除安装器登记的程序与入口，默认保留独立 data 和官方登录。`data/software-updates` 内备份/事务不是可随意删除的缓存；卸载后需自行保管数据。

当前 EXE 未附发布者 Windows 代码签名，不能将 SHA-256 校验当作 SmartScreen 信任或全环境验收；从项目 Release 下载并核对 SHA256SUMS。原生 CI 与实际官方登录/模型调用分别记录。

## 全局智能选择与窗口模型

设置中的全局智能选择分别保存文字、生图的模型、准确档位与顺序；各窗口手选和窗口候补优先。全局编辑不会重写已经排队的任务，额度信息不确认时不冒称某模型可用。运行中的任务仅在严格的本轮额度拒绝且没有正文或副作用时允许一次候补，不自动付费或切换账号。

Windows 可点同步核对官方 CLI 当前身份和模型；同步期间暂停新任务提交，前后身份不一致则拒绝采用。不会从页面选择其他账号；CC Switch 唤起仍仅适用于 Mac。实际不同账号切换与官方模型调用未纳入本轮发行验收。

## 系统代理

优先沿用启动终端的 HTTP_PROXY / HTTPS_PROXY 等代理变量。没有设置时，读取当前 Windows 用户已开启的手动系统 HTTP 代理，仅接受本机回环地址（例如 `127.0.0.1:7897`）。不会修改 Windows 设置；本机面板始终直连。

PAC 自动配置、远端代理和带账号密码的代理不会被自动搬进 CLI 环境。遇到这类网络配置，请在启动前设置 CLI 支持的代理环境，或使用自己的 TUN。修改代理后重开启动窗口。

## 与 Codex 协作

此包提供同样的 `app\bridge.py` 固定会话接口。Windows 的 Codex 在当前聊天环境有 `CODEX_THREAD_ID` 时可运行：

```powershell
& 'D:\Tools\AgyConsole\runtime\python.exe' 'D:\Tools\AgyConsole\app\bridge.py' --ensure --title '项目名称'
```

把路径改为实际解压位置。返回 URL 中的 `?codex=...` 是当前聊天的固定入口，根地址只是总览。派发示例：

```powershell
& 'D:\Tools\AgyConsole\runtime\python.exe' 'D:\Tools\AgyConsole\app\bridge.py' --task-file 'D:\Work\task.txt' --out 'D:\Work\review-01' --timeout 600
```

不写 `--model` 会尊重该固定窗口按文字/生图保存的模型与候补设置。每次使用同一聊天的环境 ID，勿借用其他聊天 ID。若 CLI 环境未提供 ID，需要由 Codex 用真实当前聊天 ID 显式传 `--thread-id`，不能按标题猜测。

完整包已附 `skills\agy-console`。双击 **Install Codex Skill.cmd**，或从开始菜单选择 **Install Codex Skill**，使用包内 Python 安装并启用默认协作。该显式入口只添加一段可移除的托管规则，保留已有指令；setup.exe 本身不会修改 Codex home。仅按需调用可省略 `--enable-default`。连接记录在本机生成；移动面板后须重新运行 Skill 安装入口。

仅打开网页不能唤醒闲置的 Codex，也不会自动带入其他电脑的聊天绑定。显示右侧活动页取决于当前 Codex 的浏览器工具及权限。程序更新不更新安装到 Codex home 的 Skill；新 Skill 需从完整包显式刷新。

## 自检、更新和备份

- **Diagnose.cmd**：检查依赖、官方 CLI、目录和端口；不读取凭据、不调用模型。
- **Self-Test.cmd**：不调用模型。验证双管道读流、模型顺序，以及启动监管进程被终止后测试子进程是否全部退出。
- 端口被占用时拒绝重复启动，不会关闭已有服务或其他人的 CLI。
- Windows 安装版的完整升级用新的 setup.exe；便携版先关启动窗口再按说明更新程序并保留 data。程序更新器仅替换 app 中白名单程序，未开放运行中原地重载。
- 页面设置可备份聊天、图片和固定绑定；备份不含官方登录和 CLI 内部上下文。跨电脑恢复面板记录不等于恢复官方会话上下文。
- 本版尚不支持从网页唤起 CC Switch；需要换号时先结束任务、关控制台，在官方 CLI 中完成登录后重开。
- 外部终端的锁文件与进程映射在 Windows 未实现；无法证明的外部活动显示未知，当前面板派发的任务仍有真实排队、进度、回复和历史。

## 验证范围

最终准确提交在 Windows 原生 CI 验证管道、ConPTY、Job、UTF-8 及会议、同步、全局模型、导出契约，并编译真实 EXE。真实公开 .24、.23、.16 和 .8 安装器分别升级至本版，检查完整源码/新 bootstrap/新模块/九文件 Skill、含空格路径、数据保留、运行维护互斥与卸载；结果见准确提交 Actions 与 Release 附件 PLATFORM-VERIFICATION.json。

这些隔离检查不登录账号或调用真实模型，不代表旧 Windows、Defender、SmartScreen、睡眠唤醒或全新硬件已验收。原始输入及包含冻结材料的完整 prompt 分别限制为 26000 个 UTF-16 单元；验证后入队使用同一冻结快照。发布者未附 Windows 代码签名。

## 来源与文件

`runtime`：官方 CPython Windows 嵌入版，以及 PyPI 的 pywinpty、psutil Windows 轮子；版本、下载链接、SHA-256 见 `THIRD-PARTY.json`，许可证随包保存。

`app`：本项目的 Windows 适配副本；不影响 Mac 上原有服务。`SHA256SUMS.txt` 记录发行文件校验值。

不包含个人账号、聊天历史、图片、项目内容、官方认证目录或 Mac 的任何启动项。首次运行后产生的 `data` 是你的持久数据，请自行备份后再删除。

## 内置更新

本版须使用完整 setup 或完整便携包升级，先关闭工具并保留 data，不提供 program-only ZIP 或更新 feed。设置保留版本检查与原有偏好，程序不会自动安装 Codex Skill。完整升级的原生 CI 结果、公开基线与安装检查见本版 Release 和 PLATFORM-VERIFICATION.json；真实登录、模型与睡眠唤醒的验收范围另列。

设置 → 配置迁移：导出配置 JSON，在其他设备导入并预览确认。包含界面、全局模型与更新偏好，可选将文字/生图模型、候补排序和协作开关应用到目标聊天；不包含聊天、绑定、官方登录。原配置自动另存，导入不支持的模型会拒绝。

0.1.0-preview.4：输入框下合并为单一队列/状态入口；共享首页可展开候补配置，先选择目标聊天再保存，文字与生图独立。

0.1.0-preview.5：修复已完成产物任务继承旧额度错误的误报；不重跑已完成的操作。未自动候补时显示具体原因。

0.1.0-preview.6：宽屏和窄屏均保留菜单按钮，可收起侧栏扩大聊天空间。

发布源已配置为 https://github.com/dneo9527-ctrl/agy-console ，通道 preview，自动下载默认关闭。自有源码采用 MIT（包内 LICENSE），第三方许可独立保留。发布端另修复回环绑定的反向 DNS 等待。

## .25：输出、材料和上下文错误

- Markdown 使用有界前向扫描，行内代码保持字面内容，HTTP(S) 链接支持成对括号并继续转义 HTML；保留图片、表格和代码复制行为。
- 圆桌长材料除首尾外最多保留四处关键原文摘录，标记原始行号、字符位置与省略数量；短材料剩余份额公平分配，完整双层 JSON 仍限 12,000 字符。已冻结旧输入和候补快照保持不变。
- 明确上下文超长时保留失败、原始错误及原会话，不自动换模型或新建会话；明确额度耗尽与速率限制保守区分，未知格式保留原提示。
- 摘录不保证保留全部证据，字符预算不清除官方历史，也不能证明 token 节省。配套 external-ai-team 仍为未修改的 preview.7，须显式安装。
