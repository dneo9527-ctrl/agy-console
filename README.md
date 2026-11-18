> **0.1.0-preview.13 开发者预览候选。** 完整包内含 Agy 配套 Skill 和 external-ai-team preview.3；从旧 .8 首次升级需完整包。发布状态和准确验证记录以对应 Release 为准。

# Agy Console · Codex × Antigravity 联动面板

本机网页控制台：通过官方 Antigravity CLI 派发任务、续接会话、查看进度，并让每个 Codex 聊天保留自己的固定协作入口。

**v0.1.0-preview.13 · macOS / Windows / Linux 开发者预览 · MIT**

- **连续协作**：固定聊天绑定、工作会话、任务队列与采纳记录；简短状态不会冒充模型完成。
- **模型与候补**：文字、生图分别选择模型和档位；按保存顺序处理明确的额度耗尽。
- **多端维护**：版本检查、可选自动下载、启动应用与回滚；配置 JSON 导出、预览导入。

独立社区项目，不是 Google、OpenAI 或其他厂商的官方产品。官方 CLI 由使用者自行安装并登录，不随本项目分发。

## 下载与启动

[下载 v0.1.0-preview.13](https://github.com/dneo9527-ctrl/agy-console/releases/tag/v0.1.0-preview.13) · [测试与构建](https://github.com/dneo9527-ctrl/agy-console/actions/workflows/checks.yml)

Windows 普通用户选择完整安装向导，已安装用户关闭本工具后运行完整安装包升级；Mac/Linux 提供需 Python 的完整源码预览。旧 `.8` bootstrap 不接受新增模块，本版默认不发 program.zip 或自动更新清单；Mac/Linux 首次升级按 [完整升级说明](docs/INSTALL.md) 保留数据后使用完整目录。

| 平台 | 安装包 | 要求与验证范围 |
| --- | --- | --- |
| macOS | [Mac 源码预览](https://github.com/dneo9527-ctrl/agy-console/releases/download/v0.1.0-preview.13/AgyConsole-0.1.0-preview.13-macos-source-preview.zip) | Python 3.9+；arm64 本机回归通过，Intel/旧系统未实机验证；无 Developer ID/公证 |
| Windows | [Windows x64 安装向导](https://github.com/dneo9527-ctrl/agy-console/releases/download/v0.1.0-preview.13/AgyConsole-0.1.0-preview.13-windows-x64-setup.exe) · [完整便携包](https://github.com/dneo9527-ctrl/agy-console/releases/download/v0.1.0-preview.13/AgyConsole-0.1.0-preview.13-windows-x64-preview.zip) | 内置 Python 3.13.17；Windows 10 1809+/11 x64 目标；无发布者代码签名；原生 CI 与真实登录分开记录 |
| Linux | [Linux 源码预览](https://github.com/dneo9527-ctrl/agy-console/releases/download/v0.1.0-preview.13/AgyConsole-0.1.0-preview.13-linux-source-preview.zip) | Python 3.9+；x64/arm64 为源码预览，原生验收待 CI/实机记录 |

1. 安装并登录 [官方 Antigravity CLI](https://antigravity.google/docs/install?tab=cli)，无需为每个聊天重复登录。
2. 完整解压到可写目录。Mac 双击 `启动控制台.command`；Linux 在终端执行 `./Start-Agy.sh`；Windows 用 `Start-Agy.cmd`。
3. 打开 `http://127.0.0.1:8765/`，保持启动终端打开。关闭终端会结束服务和它启动的 CLI；关闭网页只关闭页面。
4. 需要 Codex 持续协作时，Mac 双击 `Install Codex Skill.command`，Windows 双击 `Install Codex Skill.cmd`，Linux 运行 `python3 scripts/install_codex_skill.py --enable-default`。完整包已包含 Skill；[安装与接入说明](docs/CODEX-SKILL.md)。

Windows 安装向导创建桌面/开始菜单入口，首次指南提供官方 CLI 安装与登录入口；数据在 `%LOCALAPPDATA%\AgyConsole\data`，升级/卸载默认保留。便携版保留自身 data，不自动合并。运行中需先关本工具再升级/卸载。详见 [Windows 说明](windows/README.md)。

端口被占用时拒绝重复启动。聊天和设置保存在自己的 `data` 中；首次启动不导入旧工程历史。详见 [安装与更新](docs/INSTALL.md)。

## v0.1.0-preview.13

- 每聊天独立保存总控及 Agy／Muse／Claude 提供方选择；总暂停和单方关闭各自保留。受控前台启动需明确有效入口、允许开关和本机健康检查，不默认打开。
- 圆桌主持、两轮计划和角色快照可保存；Codex 指令有独立标识，实际观察到的 Muse／Claude 发言和总结可同步到同一聊天流并保留原链接。
- 紧凑聊天布局及白色主题改善小窗口空间；设置、草稿、窗口模型、官方会话和自动打开偏好独立保存。
- Mac 内含已隔离验证的 [Bark 完成／最终失败接口](docs/BARK.md)。接收端正在纠正 Antigravity.app 接入范围，Build 51 暂停发行，当前不作为已开放的手机提醒功能；不影响面板及配套 Skill 的独立完整安装。

**当前 Skill 没有自动跨网页圆桌调度、后台网页采集或闲置唤醒。** 记录同步只接受活跃 Codex 实际观察到的发言，当前需要一个真实完成的 Agy 圆桌轮次作为锚点；开关不会自动开会。完整八文件 external-ai-team 的显式安装见 [配套 Skill](docs/CODEX-SKILL.md)。

## v0.1.0-preview.8 历史功能

区分本轮工具权限拒绝与历史额度错误：权限拒绝保留失败状态，单独显示原因；不会仅凭继承的旧报错误封模型或自动重跑。确有本轮额度拒绝且满足同账号、同会话、无正文或副作用等约束时，最多候补一次。新增五种整体配色，整理候补区域和配置导入/导出。

菜单按钮在宽屏与窄屏常驻，可折叠侧栏；修复已完成任务继承历史额度错误的误报，仅在严格的官方产物证据满足时修正状态，不重跑模型。

合并重复的队列/终端入口；共享总览的候补配置可展开后明确选择目标聊天，固定聊天直接配置自身。文字与生图独立，离线可查看，保存时验证目标。

侧栏仅在发现新版时显示提醒，点击查看更新详情。设置里的紧凑版本卡片提供检查更新与自动下载开关。配置 JSON 可跨设备迁移界面、全局模型及更新偏好；窗口模型、候补和协作开关由用户选择是否应用，不携带聊天、绑定或凭据。

自动下载默认关闭。检查公开 GitHub Release；开启后在空闲时下载，校验后在下次前台启动应用，失败回滚。程序更新不包含 Python、稳定启动器或用户数据。摘要与 HTTPS 校验不等于开发者签名。详见 [更新协议](UPDATE-PROTOCOL.md)、[改动记录](CHANGELOG.md)。

## 与 Codex 连接

完整包内附 `agy-console` Skill 和安装入口；无需自行编写协作指令。安装入口会保留原规则，添加可移除的默认协作段。仅按需使用可不启用默认协作，详见 [Skill 说明](docs/CODEX-SKILL.md)。已有面板可[单独下载 Skill 包](https://github.com/dneo9527-ctrl/agy-console/releases/download/v0.1.0-preview.13/AgyConsole-0.1.0-preview.13-codex-skill.zip)。

手动调试连接时，先启动面板，再在当前 Codex 聊天环境运行（路径改为实际位置）：

```sh
python3 /path/to/AgyConsole/bridge.py --ensure --title '项目名称'
python3 /path/to/AgyConsole/bridge.py --task-file /path/to/task.txt --out /path/to/new-result --timeout 600
```

Windows 使用包内 `runtime/python.exe` 与 `app/bridge.py`。`CODEX_THREAD_ID` 必须来自真实当前聊天；固定 URL 中的 `?codex=...` 对应本聊天。默认不传 `--model`，保留窗口选择与候补。打开网页本身不会唤醒闲置的 Codex。

## 验证与边界

本版开发交接的完整冻结包为 191 个文件，348 项 Python、33 组 JS、7 项发行框架检查通过；发布候选另内置准确八文件 external-ai-team，并对最终提交执行多平台 CI。最终结果见对应 Release 与 Actions 记录。Windows 原生、Linux 原生、公开附件更新与下载后首次打开分别记录，不能用 Mac 的测试结果代替。[验证详情](docs/VERIFICATION.md)

仅绑定本机回环地址，适用于受信本机环境；不是多用户认证服务。聊天内容会按任务经官方 CLI 发给相应服务商。备份含私人历史，不应上传到仓库或 Issue。[隐私说明](docs/PRIVACY.md)

## 开发与许可

```sh
python3 -B scripts/audit.py --history
python3 -B scripts/check.py --report work/checks.json
python3 -B scripts/test_framework.py
```

测试夹具需要 C 编译器；程序安装和运行不需要。CI 默认只读权限，不自动发布 Release。[发布手册](docs/RELEASING.md)

本项目自有源码采用 [MIT 许可证](LICENSE)；第三方运行时的许可保持独立，见 [第三方说明](docs/THIRD-PARTY.md)。
