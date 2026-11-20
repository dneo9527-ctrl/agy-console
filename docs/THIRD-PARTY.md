# 第三方来源与许可

生产源码使用 Python 标准库；前端无需 npm 依赖。Node.js 仅用于开发测试，不随包分发。

Windows 便携包包含 CPython 3.13.17 x64、pywinpty 3.1.2 和 psutil 7.2.2。固定上游来源与 SHA-256 见 `windows/THIRD-PARTY.json`。发布方已重新核对三个原始归档与锁定摘要；包内保留 CPython LICENSE 和轮子中的许可证及 metadata。

Mac/Linux 包不包含 Python；使用者自行安装受支持的运行环境与官方 Agy CLI。官方 Antigravity、Codex 或其他厂商程序/登录不随本项目分发。

项目主图标使用原有 SVG。preview.14/.15 的 Codex、Muse 服务身份图标来自各自官方站点，原始来源、大小与 SHA-256 见 [开发候选说明](DEVELOPMENT-PREVIEW15.md)；嵌入 app.js，不发起额外请求。厂商商标权仍属原权利方，名称和图标用于标识兼容服务，不代表官方关系。

本项目自有源码按所有者明确选择采用 MIT，见根目录 LICENSE。第三方许可不会随本项目选择而改变。

来源：[CPython Windows](https://www.python.org/downloads/windows/)、[pywinpty](https://github.com/andfoy/pywinpty)、[psutil](https://github.com/giampaolo/psutil)。

## QUDAD roundtable design reference

[QUDAD-ENT/agy-roundtable](https://github.com/QUDAD-ENT/agy-roundtable), MIT, commit `b2cc2c75292eaee48cf3e7f8aea80bf4193e65d9`: stage separation and bounded shared-evidence design reference only. This candidate implements its own helper; no upstream code or assets were copied. OpenScience integration is deferred.
