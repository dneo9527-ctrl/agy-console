# Bark 可选接口

本版保留 Mac Console 的完成／最终失败事件接口，程序和两套 Skill 可独立完整安装。该可选接口不等于 Antigravity.app 原生接入，也不以手机通知作为本版验收结论。

Bark 2.6.2-preview.3 / Build 52 已独立发布为原生 Antigravity.app 实验预览；Build 51 草稿继续暂停。Bark 的公开说明及真实限制见 [Bark Release](https://github.com/dneo9527-ctrl/bark-bridge-macos/releases/tag/v2.6.2-preview.3)。Console 生产者与 Build 51 的合成接口测试不等于 Build 52 或原生 App 的端到端验收。

控制台保存当前任务最终完成或最终失败后，仅向已配置的本机接收端交接结果类别和短会话标识，不发送提示词、回复、原始错误、命令或账号。中间额度候补、取消和历史不回放；失败不改变任务结果，不新增守护进程。Windows/Linux 不调用此 Mac 接口。

尚未确认真实新模型事件至手机实收、等待授权／回答、独立 CLI 或原生 App 完整行为；不要把网页或 CLI 的任意问句当作等待状态。接收端偏好、手机、静音和路由不由本版安装器改动。
