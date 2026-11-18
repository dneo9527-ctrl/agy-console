# v0.1.0-preview.6 验证范围

## 0.1.0-preview.13 配套发行候选

完整开发源码来自公开 .8 分发基线，保留 Windows 原生防护、安装器、固定依赖、许可证和配套入口，加入 .9–.13 功能、独立多 AI 模块和 Bark 完成／失败生产者。原始 191 文件交接有完整字节、Git mode 和 SHA-256 清单；发布方新增准确 external-ai-team preview.3 八文件的 POSIX／Windows 副本，私有本机 adapter 不包含。

开发方隔离检查 348 项 Python、33 组 JS、7 项发行框架通过。发布方对最终提交另执行 macOS／Linux／Windows CI、原生 Windows 安装器和隔离连接验收；最终通过状态见 Release 与准确提交的 Actions，不能提前用旧 .8 的下方回执替代。

当前 Skill 验收覆盖 schema1、提供方门控、单入口路由和受控前台启动；自动跨网页圆桌未集成。圆桌真实发言同步由活跃 Codex 观察后执行，不是后台采集，不从元数据推断外部 AI 已参与。Bark 手机实收、真实新模型触发、Windows 官方登录、冷启动和新硬件验收仍须分别记录。

旧 .8 首次升级须完整包，不提供普通 program-only 替代；程序／Skill 安装各自显式操作，保留已有数据与偏好。下方旧版证据只属于对应的历史版本。


最新开发冻结为132文件，原清单摘要见 `developer-source.json`。发布方已逐文件核对并验证9项原始产物的摘要和ZIP CRC。发布副本使用自绘SVG、中文分发文档、依赖哈希与已授权公开更新源；因而附件重新构建，不沿用开发包摘要。

本次发布另外修复：Python HTTPServer 在绑定回环地址时会反向查询主机名，真实Mac CI堆栈显示卡在系统解析器。回环服务改为直接绑定数值地址并使用localhost显示名称；Host/Origin/token检查及前台终端语义保留。回归要求socket.getfqdn抛错时仍可真实绑定随机回环端口。

| 检查 | 证据与范围 |
| --- | --- |
| 开发端 .5 Python / .6 JavaScript | 241项 / 30组通过；发布候选单独重跑 |
| .6 视觉 | 新菜单布局的真实浏览器视觉验收未运行；仅JS和HTTP结构核验，不将旧截图作新版本证据 |
| 本地发布端回归、审计、打包 | 本地证据见 publication-verification.json；按当前准确程序版本记录 |
| 远端 CI | 需要发布SHA的Apple Silicon、Intel Mac、Ubuntu与Windows原生检查通过，链接在Release说明 |
| Windows原生自检 | 管道、Job Object、ConPTY、回环绑定；不含真实CLI登录或模型调用 |
| 配置导入/更新回滚 | 隔离测试覆盖；不修改发布者现用data或登录 |
| GitHub附件与更新下载 | 发布后重新下载校验；模拟旧版本的公网下载暂存不等于真实公开版本间安装 |
| Apple签名与兼容性 | 无Developer ID/公证；新Mac检疫首开、旧系统与用户实机仍未验收 |

CI使用伪CLI、隔离HOME和随机回环端口，不发送模型请求。真实账号、聊天、配置与登录不进入源码或包。程序更新没有独立发布者签名，HTTPS与摘要验证不能冒称Apple公证或密码学发布者认证。


## 0.1.0-preview.8

开发交接的 248 项 Python 与 30 组 JavaScript 回归已通过。发布候选从公开 .7 逐段合并，另行执行源码/历史审计、合并后测试、准确提交的 Mac/Linux 与 Windows 原生 CI，以及草稿/公开附件回下核验；实际完成结果以本版本 Release 和 Actions 记录为准。测试均使用临时假数据，不读取用户登录或配置。Windows 完整安装包升级与 Mac/Linux 程序更新分开验收。
