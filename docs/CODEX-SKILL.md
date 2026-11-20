# 安装 Codex 配套 Skill

本版完整包和独立 Skill 包同时包含 `agy-console` 配套入口与严格对应的 `external-ai-team` preview.7 九文件版本。二者均需显式安装；程序更新不会改写 Codex home。多 AI 提供方开关／受控前台启动由后者按当前聊天策略读取，明确圆桌模式下的活跃任务由后者按独立会议流程组织。

面板负责运行任务，`agy-console` Skill 负责指导 Codex 在当前聊天中调用面板。完整源码、Mac/Linux 源码预览和 Windows 完整包均包含 Skill、分发脚本和安装工具。仅打开网页不会让闲置的 Codex 自动执行工作。

先把面板放到固定的可写目录，安装并登录官方 Antigravity CLI。Skill 不包含账号，不会启动服务或调用模型；运行任务时仍需保持面板的前台终端打开。

## 安装与默认协作

- Mac：在完整解压的面板目录双击 `Install Codex Skill.command`。
- Windows：在面板目录双击 `Install Codex Skill.cmd`，或在开始菜单选 **Install Codex Skill**。使用包内 Python，无需另装 Python。
- Linux：在完整面板目录执行 `python3 scripts/install_codex_skill.py --enable-default`。

这些入口会安装 `agy-console`，并向当前 Codex home 的 `AGENTS.md` 添加一段有起止标记的默认协作规则。其余规则保留；更改前保留备份。如果只想安装 Skill、按需调用，使用下面命令并省略 `--enable-default`。

```sh
python3 scripts/install_codex_skill.py --codex-home /path/to/codex-home
```

默认使用环境变量 `CODEX_HOME`，未设置则使用用户主目录下的 `.codex`。多个独立 Codex home 需分别接入。Windows 可在 PowerShell 中使用：

```powershell
& '.\runtime\python.exe' '.\install_codex_skill.py' --codex-home 'D:\Codex Home' --enable-default
```

重新打开或新建 Codex 聊天后，使用 `$agy-console`，或让它按已启用的默认规则处理实质工作。Skill 会从当前环境读取 `CODEX_THREAD_ID`；不会猜测、借用其他聊天 ID。无 ID 的环境不受此安装器支持。

## 已有安装与单独下载

Skill 固定连接到安装时选择的面板目录与 Python。移动面板、更换运行时或更新 Skill 后，重新运行安装器刷新连接。只替换带本工具安装标记的同名 Skill，旧副本保留为备份；遇到其他来源的同名 Skill 或链接目录会停止。

Release 中的 `AgyConsole-0.1.0-preview.24-codex-skill.zip` 适合已有兼容面板的用户。完整解压后，将下面命令的路径换成现有面板目录；不要直接把模板复制进 Codex 的 skills 目录：

```sh
python3 scripts/install_codex_skill.py --console-root /path/to/AgyConsole --enable-default
```

Windows 使用现有面板的 `runtime\python.exe` 运行该脚本，并通过 `--console-root` 指向包含 `app\bridge.py` 的目录。包内也提供 `windows\install_codex_skill.py`。安装后的 `connection.json` 只保存在本机，不随发行包分发。

程序内置更新只替换面板程序，保留稳定启动器和用户安装的 Skill。Skill 的安装或更新通过上述入口显式执行，不混入程序更新事务。

## external-ai-team 的显式安装

Mac/Linux 完整包内执行：

```sh
python3 skills/external-ai-team/install.py --codex-home /path/to/codex-home
```

Windows 完整安装／便携包内执行：

```powershell
& '.\runtime\python.exe' '.\skills\external-ai-team\install.py' --codex-home 'D:\Codex Home'
```

如果下载独立 Skill 包，Windows 版本位于 `windows/skills/external-ai-team`，使用现有面板的 Python 执行。已有 `external-ai-team` 目标会被拒绝，不覆盖或删除；保留旧版本并按自己的 Skill 维护流程确认替换后再安装。本步骤不改默认规则、审批、提供方、模型、草稿或自动启动偏好。

重新打开聊天后按 `$external-ai-team` 使用。先确认当前聊天的提供方和项目会话，再调用；单纯开启多个提供方不会群发。需手动配置受支持部署的连接位置，包中不包含本机私有 adapter、真实策略或账户。完整九文件版本也随 [codex-skills preview.6](https://github.com/dneo9527-ctrl/codex-skills/releases/tag/v0.1.0-preview.6) 同步发行。

明确选择圆桌模式后，活跃 Codex 按 `references/roundtable.md` 创建独立会议，记录真实提案、互评和选定主持总结；默认 assist 仍使用单提供方。每次新外发都检查准确聊天策略并保护草稿和已有生成。网页贡献不依赖 Agy 成功，Agy 贡献关联真实任务；发送结果不明时核对原会话，不盲目重发。安装不配置主持或开启提供方，不采集后台网页，也不唤醒闲置聊天。

## 偏好、验证与移除

每个聊天保留自己的持续协作、自动打开活动页、文字/生图模型和候补设置。关闭持续协作或要求“Codex only”后，不再派发新的协作任务。安装器不更改这些偏好，不改 Codex 审批权限，不接触官方登录。

可让 Codex 执行安装后 Skill 的 Local connection 命令并加 `--ensure`：应返回本聊天固定 URL，且 `model_started:false`。这只验证连接；要验证模型需另发一个已授权任务。实际回复、任务状态、会话 ID 与采纳记录才是协作完成的证据。

不再使用时，关闭面板；移除本工具安装的 `skills/agy-console`，并只删除 `AGENTS.md` 中 `agy-console:managed:start` 到 `agy-console:managed:end` 的托管段。保留其余项目规则与备份。卸载 Windows 程序会保留 Codex home 中的 Skill；不要再调用已经不存在的连接，需自行移除或连接到新目录。
