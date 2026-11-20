> **升级门槛：preview.16 及更早 → preview.22 必须先使用完整包。** 旧稳定 bootstrap 不接受新模块 `transcript_export.py`，且普通程序包不会更新 bootstrap。关闭原工具、保留数据后完整升级；本候选不发布 program ZIP 或 updates feed。

> Preview.8 → preview.13 requires a complete package: the old bootstrap does not accept multi_ai_collaboration.py. Program-only updates are disabled by default for this release candidate.

# Agy Console 更新协议 v1

程序版本从 `VERSION` 读取，采用语义化版本，例如 `0.1.0-preview.1`。每次正式交付递增版本，GitHub 标签为 `v` 加该版本。稳定通道忽略预览版本，预览通道可接收更高版本；禁止自动降级或应用相同版本。

发布者应在干净的完整安装包内设置 `release-source.json`：

```json
{"repository":"OWNER/REPO","channel":"preview"}
```

未确定仓库时保留空字符串，软件显示“发布源待配置”，不向猜测仓库发请求。用户也可在设置中填写自己的官方发布仓库；不保存 GitHub 令牌。只支持公开 Releases。私有仓库认证不在 v1 范围。

每个 Release 上传 `updates.json` 与对应的程序更新 ZIP，标签、清单、ZIP 内 VERSION 必须一致：

```json
{
  "schema":1,
  "version":"0.1.1-preview.1",
  "platforms":{
    "windows-x64":{"file":"AgyConsole-0.1.1-preview.1-windows-x64-program.zip","sha256":"64位小写SHA256","bytes":12345}
  }
}
```

平台键：`macos-arm64`、`macos-x64`、`windows-x64`、`linux-arm64`、`linux-x64`。只为实际构建的产物添加键，不用别的平台包兜底。平台未发布程序更新包时，不会下载对应完整安装包覆盖运行时。

生成命令：

```text
python3 build_update.py --app-dir /干净程序目录 --out /新发布目录 --target macos-arm64
```

Windows 的 `--app-dir` 指向完整便携包内的 `app`。同一版本的各平台命令可向同一个新发布目录追加 `updates.json` 平台项。本脚本仅构建本地文件，不推送、不上传、不发布。

ZIP 扁平存放程序允许列表与 `payload.json`，后者包含 schema、version、target、逐文件 SHA256。下载包和解压后总量均限制 32 MiB，拒绝未知文件、目录穿越、重复条目、符号链接、加密包、语法无效的 Python、版本不一致和错误平台。使用 GitHub 官方 HTTPS 地址，校验 Release 附件大小与可用的官方 digest，再校验整个 ZIP 及每个文件的 SHA256。这是传输完整性校验，不能冒称开发者签名；有源仓库写入权的发布者仍是受信任方。正式公证、开发者签名或签名清单是另外的发布流程。

检查仅在原控制台进程内运行；正常每天最多自动检查一次，手动按钮可主动检查。自动更新默认关闭。启用后空闲时自动下载；检查、下载与暂存均不替换运行中的程序。下载失败不会无限自动重试，需下次检查或手动重试。

稳定 `update_bootstrap.py` 在服务导入前处理更新。Mac/Linux 启动文件先执行它，Windows 由现有前台监管器的 Job Object 内运行它。程序允许列表不含 bootstrap、启动监管器、Python 运行时、官方 CLI、`data`、凭据、项目文件和用户更新源。协议或运行时需要升级时应提供完整安装包，不能强塞进普通更新。

应用前备份原程序并持久化回滚日志，逐文件原子替换；程序成功绑定本机端口并初始化后确认提交。应用中断、启动失败或未确认时，下次启动首先回滚原程序。替换失败也立即回滚；回滚证据损坏时拒绝继续写入。数据格式迁移与这套代码回滚不同；v1 不执行不可逆数据迁移，新版本必须继续读取旧数据或另行设计迁移/回滚。

旧 `ai-assistants/runs` 内容不随安装或更新导入。确需兼容迁移时，用户显式使用 `--import-legacy-history`；它不读取官方登录凭据。已有面板 `data` 始终保留。

验收至少包括：版本/通道/平台过滤、任务忙时不下载、哈希与恶意 ZIP 拒绝、暂存不改运行源、启动应用与确认、中断回滚、数据保持、原终端关闭仍退出、重复点击不并发、真实发布附件下载与最低系统实机测试。单元/隔离测试不能代替 Windows/Linux 实机验证。

官方接口依据：[GitHub Releases API](https://docs.github.com/en/rest/releases/releases)、[Release assets API](https://docs.github.com/en/rest/releases/assets)。
