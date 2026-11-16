# 安装 Codex 配套 Skill

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

Release 中的 `AgyConsole-0.1.0-preview.8-codex-skill.zip` 适合已有面板的用户。完整解压后，将下面命令的路径换成现有面板目录；不要直接把模板复制进 Codex 的 skills 目录：

```sh
python3 scripts/install_codex_skill.py --console-root /path/to/AgyConsole --enable-default
```

Windows 使用现有面板的 `runtime\python.exe` 运行该脚本，并通过 `--console-root` 指向包含 `app\bridge.py` 的目录。包内也提供 `windows\install_codex_skill.py`。安装后的 `connection.json` 只保存在本机，不随发行包分发。

程序内置更新只替换面板程序，保留稳定启动器和用户安装的 Skill。Skill 的安装或更新通过上述入口显式执行，不混入程序更新事务。

## 偏好、验证与移除

每个聊天保留自己的持续协作、自动打开活动页、文字/生图模型和候补设置。关闭持续协作或要求“Codex only”后，不再派发新的协作任务。安装器不更改这些偏好，不改 Codex 审批权限，不接触官方登录。

可让 Codex 执行安装后 Skill 的 Local connection 命令并加 `--ensure`：应返回本聊天固定 URL，且 `model_started:false`。这只验证连接；要验证模型需另发一个已授权任务。实际回复、任务状态、会话 ID 与采纳记录才是协作完成的证据。

不再使用时，关闭面板；移除本工具安装的 `skills/agy-console`，并只删除 `AGENTS.md` 中 `agy-console:managed:start` 到 `agy-console:managed:end` 的托管段。保留其余项目规则与备份。卸载 Windows 程序会保留 Codex home 中的 Skill；不要再调用已经不存在的连接，需自行移除或连接到新目录。
