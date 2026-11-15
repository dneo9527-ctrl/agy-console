# Agy Console · Codex × Antigravity 联动面板

本机网页控制台：通过官方 Antigravity CLI 派发任务、续接会话、查看进度，并让每个 Codex 聊天保留自己的固定协作入口。

**v0.1.0-preview.3 · 开发者预览 · MIT**

- **连续协作**：固定聊天绑定、工作会话、任务队列与采纳记录；简短状态不会冒充模型完成。
- **模型与候补**：文字、生图分别选择模型和档位；按保存顺序处理明确的额度耗尽。
- **多端维护**：版本检查、可选自动下载、启动应用与回滚；配置 JSON 导出、预览导入。

独立社区项目，不是 Google、OpenAI 或其他厂商的官方产品。官方 CLI 由使用者自行安装并登录，不随本项目分发。

## 下载与启动

[下载 v0.1.0-preview.3](https://github.com/dneo9527-ctrl/agy-console/releases/tag/v0.1.0-preview.3) · [测试与构建](https://github.com/dneo9527-ctrl/agy-console/actions/workflows/checks.yml)

请选择完整包；`*-program.zip` 仅供内置更新器使用。

| 平台 | 安装包 | 要求与验证范围 |
| --- | --- | --- |
| macOS | [Mac 源码预览](https://github.com/dneo9527-ctrl/agy-console/releases/download/v0.1.0-preview.3/AgyConsole-0.1.0-preview.3-macos-source-preview.zip) | Python 3.9+；arm64 本机回归通过，Intel/旧系统未实机验证；无 Developer ID/公证 |
| Windows | [Windows x64 便携预览](https://github.com/dneo9527-ctrl/agy-console/releases/download/v0.1.0-preview.3/AgyConsole-0.1.0-preview.3-windows-x64-preview.zip) | 内置 Python 3.13.17；Windows 10 1809+/11 x64 目标；原生验收待 CI/实机记录 |
| Linux | [Linux 源码预览](https://github.com/dneo9527-ctrl/agy-console/releases/download/v0.1.0-preview.3/AgyConsole-0.1.0-preview.3-linux-source-preview.zip) | Python 3.9+；x64/arm64 为源码预览，原生验收待 CI/实机记录 |

1. 安装并登录 [官方 Antigravity CLI](https://antigravity.google/docs/install?tab=cli)，无需为每个聊天重复登录。
2. 完整解压到可写目录。Mac 双击 `启动控制台.command`；Linux 在终端执行 `./Start-Agy.sh`；Windows 用 `Start-Agy.cmd`。
3. 打开 `http://127.0.0.1:8765/`，保持启动终端打开。关闭终端会结束服务和它启动的 CLI；关闭网页只关闭页面。

端口被占用时拒绝重复启动。聊天和设置保存在自己的 `data` 中；首次启动不导入旧工程历史。详见 [安装与更新](docs/INSTALL.md)。

## v0.1.0-preview.3

侧栏仅在发现新版时显示提醒，点击查看更新详情。设置里的紧凑版本卡片提供检查更新与自动下载开关。配置 JSON 可跨设备迁移界面、全局模型及更新偏好；窗口模型、候补和协作开关由用户选择是否应用，不携带聊天、绑定或凭据。

自动下载默认关闭。检查公开 GitHub Release；开启后在空闲时下载，校验后在下次前台启动应用，失败回滚。程序更新不包含 Python、稳定启动器或用户数据。摘要与 HTTPS 校验不等于开发者签名。详见 [更新协议](UPDATE-PROTOCOL.md)、[改动记录](CHANGELOG.md)。

## 与 Codex 连接

先启动面板，再在当前 Codex 聊天环境运行（路径改为实际位置）：

```sh
python3 /path/to/AgyConsole/bridge.py --ensure --title '项目名称'
python3 /path/to/AgyConsole/bridge.py --task-file /path/to/task.txt --out /path/to/new-result --timeout 600
```

Windows 使用包内 `runtime/python.exe` 与 `app/bridge.py`。`CODEX_THREAD_ID` 必须来自真实当前聊天；固定 URL 中的 `?codex=...` 对应本聊天。默认不传 `--model`，保留窗口选择与候补。持续协作还需要使用者自己的 Skill/项目指令；打开网页本身不会唤醒闲置的 Codex。

## 验证与边界

开发交接记录 235 项 Python、30 组 JS 和 19 项配置/更新检查通过；发布候选会单独重跑。Windows 原生、Linux 原生、公开附件更新与下载后首次打开分别记录，不能用 Mac 的测试结果代替。[验证详情](docs/VERIFICATION.md)

仅绑定本机回环地址，适用于受信本机环境；不是多用户认证服务。聊天内容会按任务经官方 CLI 发给相应服务商。备份含私人历史，不应上传到仓库或 Issue。[隐私说明](docs/PRIVACY.md)

## 开发与许可

```sh
python3 -B scripts/audit.py --history
python3 -B scripts/check.py --report work/checks.json
python3 -B scripts/test_framework.py
```

测试另需 C 编译器（用于真实控制终端夹具，不是安装程序的依赖）。CI 默认只读权限，不自动发布 Release。[发布手册](docs/RELEASING.md)

本项目自有源码采用 [MIT 许可证](LICENSE)；第三方运行时的许可保持独立，见 [第三方说明](docs/THIRD-PARTY.md)。
