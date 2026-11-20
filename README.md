> **preview.24 及更早升级到 preview.25，必须使用完整包。** 先关闭原工具、备份并保留 data；本版不提供 program-only ZIP 或 updates.json。Mac/Linux 需 Python 3.9+，Windows 完整包内置 Python。

# Agy Console · Codex × Antigravity 联动面板

本机网页控制台：通过官方 Antigravity CLI 派发任务、续接会话、查看进度，并让每个 Codex 聊天保留自己的固定协作入口。

**v0.1.0-preview.25 · macOS / Windows / Linux 开发者预览 · MIT**

- **连续协作**：固定聊天绑定、工作会话、任务队列与采纳记录；简短状态不会冒充模型完成。
- **模型与候补**：文字、生图分别选择模型和档位；按保存顺序处理明确的额度耗尽。
- **多端维护**：完整包升级与数据保留；配置 JSON 导出、预览导入，文字和生图独立的全局智能名单。

独立社区项目，不是 Google、OpenAI 或其他厂商的官方产品。官方 CLI 由使用者自行安装并登录，不随本项目分发。

## 下载与启动

[下载 v0.1.0-preview.25](https://github.com/dneo9527-ctrl/agy-console/releases/tag/v0.1.0-preview.25) · [测试与构建](https://github.com/dneo9527-ctrl/agy-console/actions/workflows/checks.yml)

Windows 普通用户选择完整安装向导，已安装用户关闭本工具后运行完整安装包升级；Mac/Linux 提供需 Python 的完整源码预览。旧版升级须更换完整程序与启动文件，不能假定原 bootstrap 接受全部模块，本版禁止生成或发布 program-only ZIP 和更新清单；Mac/Linux 首次升级按 [完整升级说明](docs/INSTALL.md) 保留数据后使用完整目录。

| 平台 | 安装包 | 要求与验证范围 |
| --- | --- | --- |
| macOS | [Mac 源码预览](https://github.com/dneo9527-ctrl/agy-console/releases/download/v0.1.0-preview.25/AgyConsole-0.1.0-preview.25-macos-source-preview.zip) | Python 3.9+；arm64 本机回归通过，Intel/旧系统未实机验证；无 Developer ID/公证 |
| Windows | [Windows x64 安装向导](https://github.com/dneo9527-ctrl/agy-console/releases/download/v0.1.0-preview.25/AgyConsole-0.1.0-preview.25-windows-x64-setup.exe) · [完整便携包](https://github.com/dneo9527-ctrl/agy-console/releases/download/v0.1.0-preview.25/AgyConsole-0.1.0-preview.25-windows-x64-preview.zip) | 内置 Python 3.13.17；Windows 10 1809+/11 x64 目标；无发布者代码签名；原生 CI 与真实登录分开记录 |
| Linux | [Linux 源码预览](https://github.com/dneo9527-ctrl/agy-console/releases/download/v0.1.0-preview.25/AgyConsole-0.1.0-preview.25-linux-source-preview.zip) | Python 3.9+；x64/arm64 为源码预览，原生验收待 CI/实机记录 |

1. 安装并登录 [官方 Antigravity CLI](https://antigravity.google/docs/install?tab=cli)，无需为每个聊天重复登录。
2. 完整解压到可写目录。Mac 双击 `启动控制台.command`；Linux 在终端执行 `./Start-Agy.sh`；Windows 用 `Start-Agy.cmd`。
3. 打开 `http://127.0.0.1:8765/`，保持启动终端打开。关闭终端会结束服务和它启动的 CLI；关闭网页只关闭页面。
4. 需要 Codex 持续协作时，Mac 双击 `Install Codex Skill.command`，Windows 双击 `Install Codex Skill.cmd`，Linux 运行 `python3 scripts/install_codex_skill.py --enable-default`。完整包已包含 Skill；[安装与接入说明](docs/CODEX-SKILL.md)。

Windows 安装向导创建桌面/开始菜单入口，首次指南提供官方 CLI 安装与登录入口；数据在 `%LOCALAPPDATA%\AgyConsole\data`，升级/卸载默认保留。便携版保留自身 data，不自动合并。运行中需先关本工具再升级/卸载。详见 [Windows 说明](windows/README.md)。

端口被占用时拒绝重复启动。聊天和设置保存在自己的 `data` 中；首次启动不导入旧工程历史。详见 [安装与更新](docs/INSTALL.md)。

## v0.1.0-preview.25

- Markdown 使用有界前向扫描，行内代码保持字面内容，HTTP(S) 链接支持成对括号并继续转义 HTML；保留图片、表格和代码复制行为。
- 圆桌长材料除首尾外最多保留四处关键原文摘录，标记原始行号、字符位置与省略数量；短材料剩余份额公平分配，完整双层 JSON 仍限 12,000 字符。已冻结旧输入和候补快照保持不变。
- 明确上下文超长时保留失败、原始错误及原会话，不自动换模型或新建会话；明确额度耗尽与速率限制保守区分，未知格式保留原提示。
- 摘录不保证保留全部证据，字符预算不清除官方历史，也不能证明 token 节省。配套 external-ai-team 仍为未修改的 preview.7，须显式安装。

[详细功能说明](使用说明.md)

## preview.24 延续功能

- 五种整体配色覆盖设置、菜单和控件；紧凑协作设置，配置按钮为“导出／导入”，模型菜单仅一个“全局智能选择”入口。
- 本场发言可按参与者筛选；主持显式记录“建议采用、分歧、待验证、下一步”，每项可跳转本场真实来源。建议不代表用户批准或全员共识。
- 按需打开材料抽屉，查看实际 CLI 入队快照及已知输入／输出／缓存用量；网页用量未知单列，完整官方历史保留，不承诺 token 节省比例。
- 新会议支持讨论、代码审查、论文评审三种冻结预设；旧会不回填，完全相同正文可引用复用而保留每条来源。
- 完整包携带新增必需模块及匹配的 `external-ai-team` preview.7 九文件 Skill；保留 .23 的平台与发行修正。Skill 显式安装，原规则和偏好保留；OpenScience 仍延期。

圆桌由活跃 Codex 与可用的项目会话、官方 CLI、宿主浏览器工具组织；开关不会唤醒闲置聊天。Windows/Linux 真实登录与模型、实际换另一个账号、睡眠唤醒和全平台自动展开仍未验收。见[配套 Skill](docs/CODEX-SKILL.md)与[验证范围](docs/VERIFICATION.md)。

## v0.1.0-preview.8 历史功能

区分本轮工具权限拒绝与历史额度错误：权限拒绝保留失败状态，单独显示原因；不会仅凭继承的旧报错误封模型或自动重跑。确有本轮额度拒绝且满足同账号、同会话、无正文或副作用等约束时，最多候补一次。新增五种整体配色，整理候补区域和配置导入/导出。

菜单按钮在宽屏与窄屏常驻，可折叠侧栏；修复已完成任务继承历史额度错误的误报，仅在严格的官方产物证据满足时修正状态，不重跑模型。

合并重复的队列/终端入口；共享总览的候补配置可展开后明确选择目标聊天，固定聊天直接配置自身。文字与生图独立，离线可查看，保存时验证目标。

侧栏仅在发现新版时显示提醒，点击查看更新详情。设置里的紧凑版本卡片提供检查更新与自动下载开关。配置 JSON 可跨设备迁移界面、全局模型及更新偏好；窗口模型、候补和协作开关由用户选择是否应用，不携带聊天、绑定或凭据。

自动下载默认关闭。检查公开 GitHub Release；开启后在空闲时下载，校验后在下次前台启动应用，失败回滚。程序更新不包含 Python、稳定启动器或用户数据。摘要与 HTTPS 校验不等于开发者签名。详见 [更新协议](UPDATE-PROTOCOL.md)、[改动记录](CHANGELOG.md)。

## 与 Codex 连接

完整包内附 `agy-console` Skill 和安装入口；无需自行编写协作指令。安装入口会保留原规则，添加可移除的默认协作段。仅按需使用可不启用默认协作，详见 [Skill 说明](docs/CODEX-SKILL.md)。已有面板可[单独下载 Skill 包](https://github.com/dneo9527-ctrl/agy-console/releases/download/v0.1.0-preview.25/AgyConsole-0.1.0-preview.25-codex-skill.zip)。

手动调试连接时，先启动面板，再在当前 Codex 聊天环境运行（路径改为实际位置）：

```sh
python3 /path/to/AgyConsole/bridge.py --ensure --title '项目名称'
python3 /path/to/AgyConsole/bridge.py --task-file /path/to/task.txt --out /path/to/new-result --timeout 600
```

Windows 使用包内 `runtime/python.exe` 与 `app/bridge.py`。`CODEX_THREAD_ID` 必须来自真实当前聊天；固定 URL 中的 `?codex=...` 对应本聊天。默认不传 `--model`，保留窗口选择与候补。打开网页本身不会唤醒闲置的 Codex。

## 验证与边界

本版在公开 .24 准确提交上整合 10 个冻结源码/文档增量，保留公开测试夹具、Windows 适配及九文件 Skill .7。开发者报告 516 项 Python、41 组 JS 和本机界面验收，实际外部圆桌 partial 5/7，两次 Agy 均无正文失败，不称全员一致或 Agy 复核完成。发行方另执行准确提交回归、平台 CI、完整安装升级与下载检查。最终结果见对应 Release 与 Actions 记录。Windows 原生、Linux 原生、公开附件更新与下载后首次打开分别记录，不能用 Mac 的测试结果代替。[验证详情](docs/VERIFICATION.md)

仅绑定本机回环地址，适用于受信本机环境；不是多用户认证服务。聊天内容会按任务经官方 CLI 发给相应服务商。备份含私人历史，不应上传到仓库或 Issue。[隐私说明](docs/PRIVACY.md)

## 开发与许可

```sh
python3 -B scripts/audit.py --history
python3 -B scripts/check.py --report work/checks.json
python3 -B scripts/test_framework.py
```

测试夹具需要 C 编译器；程序安装和运行不需要。CI 默认只读权限，不自动发布 Release。[发布手册](docs/RELEASING.md)

本项目自有源码采用 [MIT 许可证](LICENSE)；第三方运行时的许可保持独立，见 [第三方说明](docs/THIRD-PARTY.md)。
