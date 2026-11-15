# v0.1.0-preview.6 验证范围

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
