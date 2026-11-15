# 第三方来源与许可

生产源码使用 Python 标准库；前端无需 npm 依赖。Node.js 仅用于开发测试，不随包分发。

Windows 便携包包含 CPython 3.13.17 x64、pywinpty 3.1.2 和 psutil 7.2.2。固定上游来源与 SHA-256 见 `windows/THIRD-PARTY.json`。发布方已重新核对三个原始归档与锁定摘要；包内保留 CPython LICENSE 和轮子中的许可证及 metadata。

Mac/Linux 包不包含 Python；使用者自行安装受支持的运行环境与官方 Agy CLI。官方 Antigravity、Codex 或其他厂商程序/登录不随本项目分发。

发布候选使用本项目代码绘制的 SVG 图标，原来源未确认的嵌入 PNG/EXIF 资源未进入重建产物。厂商名称仅作兼容说明，不代表官方关系。

本项目自有源码按所有者明确选择采用 MIT，见根目录 LICENSE。第三方许可不会随本项目选择而改变。

来源：[CPython Windows](https://www.python.org/downloads/windows/)、[pywinpty](https://github.com/andfoy/pywinpty)、[psutil](https://github.com/giampaolo/psutil)。
