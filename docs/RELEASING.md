# 发布手册

首次公开发布为 v0.1.0-preview.4，所有者已明确选择公开＋MIT。后续版本沿用本项目既有授权及用户最新指令；每次依据冻结源码、准确提交 CI 和对应平台附件执行。`release.json` 不是授权本身，填写须依据实际指令与证据。

## 1. 接收版本交接

开发交接至少收齐：版本和渠道、源码 commit 或精确文件摘要、改动说明、逐平台测试、最低系统与架构、数据迁移/回滚、已知限制和更新协议。具体本机路径、开发聊天 ID 和私人审查记录保留在本地工作记录，不放公开包。

开发源码仍在变动时不要发布。用 `scripts/export_source.py --from-dir SOURCE --expected FROZEN_FILES_JSON --out NEW_EMPTY_DIRECTORY` 一次验证全部摘要后导出。新增模块或测试必须先审阅，再显式加入 `release-files.json`，不会自动递归拾取。

此导出先放到独立目录；审查后与框架合并，保留本框架文档与脚本。当前简易 SVG 图标为自绘素材，开发方图标来源未确认前不替换。`source-freeze.json` 记录最终公开程序与测试文件摘要，`release.json.source_manifest_sha256` 记录该清单的摘要。程序、前端和包内版本必须与交接一致。

## 2. 明确项目属性

重新只读核对 GitHub 身份及仓库权限，确认目标 OWNER/REPO、公开/私有和许可证。没有依据时不创建公开仓库，不自动写 MIT。本项目许可证确定后新增 LICENSE，并显式加入源码允许列表。

新仓库先形成干净本地 Git 历史。保留原工程，不上传现用安装目录；新工作分支用 `codex/` 前缀。逐个添加审核文件，不用全目录 `git add .`。只推送经审核的历史，不 force push、不移动已发布标签。

## 3. 本地与远端验收

```sh
python3 scripts/audit.py --history --report work/audit.json
python3 scripts/check.py --report work/checks.json
python3 scripts/test_framework.py
python3 scripts/release_plan.py --report work/release-plan.json
```

源码扫描只有有限覆盖。还需解包审计平台附件、第三方许可、EXIF、二进制 strings 与本机路径。保留原始字节和文件 mode；Windows CRLF、上游许可证不能因文本读取而被悄悄改写。

GitHub Actions 默认 `contents: read`，源码校验与演练不会上传 Release。Actions 依赖固定在查询过的官方 commit。参考：[GitHub 最小权限说明](https://docs.github.com/en/actions/tutorials/authenticate-with-github_token)。

发布前核对本次完整 commit SHA 的 CI 结果。当前工作流只覆盖参考源码和框架；最终 Windows/Linux 的原生构建、自检及真实端到端项目，由开发交接后补齐。任何没有运行的检查写 `not_run`，不用旧徽标代替本次结果。

## 4. 平台包与更新清单

源码包由 `scripts/package_source.py --out dist` 生成；配置未就绪会拒绝执行。`--rehearsal` 仅生成清楚标注 framework-check 的演练包，不是可发布版本。

平台包按 `packaging/README.md` 接收或构建。版本、源码摘要、文件名、大小和 SHA-256 写入 `assets-manifest.json`，执行：

```sh
python3 scripts/verify_assets.py --dir DIST --manifest ASSETS_MANIFEST_JSON
```

再生成统一 SHA256SUMS。此检查只验证字节，不代表安装、签名、升级或隐私审计通过。Mac 公证、Windows Job/ConPTY、Linux 各架构、空闲安装与失败回滚分别记录。

更新功能必须与开发方约定清单 schema、版本比较、平台键、GitHub 资源名、摘要/签名、路径保护、数据保留和回滚；摘要一致不是独立的发布者身份认证。首次发布前更新地址不可指向猜测仓库；当前不生成或发布 update.json。

## 5. GitHub 草稿与正式发布

下列命令仅供未来在配置和授权到齐后使用。GH_BIN、REPO、TAG、SHA 和附件路径由操作人根据实际交接填写；模板本身不触发任何外部操作。

```sh
GH_BIN=gh
$GH_BIN api user --jq '{login}'
$GH_BIN repo view "$REPO" --json nameWithOwner,visibility,defaultBranchRef
$GH_BIN run list --repo "$REPO" --json databaseId,headSha,status,conclusion,url
$GH_BIN run view "$RUN_ID" --repo "$REPO" --json headSha,status,conclusion,jobs,url
```

确认 `headSha` 等于 `$SHA` 且要求的工作流通过。先查 `release view`、远端 tag/ref 和附件，避免重复创建。新仓库按本项目明确选择使用 `repo create --public` 或 `--private`；本手册不默认选择。

```sh
$GH_BIN release create "$TAG" "$PACKAGE" "$SUMS" \
  --repo "$REPO" --target "$SHA" --title "$TITLE" \
  --notes-file "$NOTES" --draft --prerelease
$GH_BIN release view "$TAG" --repo "$REPO" \
  --json tagName,isDraft,isPrerelease,targetCommitish,url,assets
```

核对草稿附件名字、大小、摘要、目标 commit 与说明。预览发布示例：

```sh
$GH_BIN release edit "$TAG" --repo "$REPO" \
  --draft=false --prerelease --latest=false
```

稳定版须依据实际渠道改参数，不将预览冒称稳定最新版。多行说明用文件参数。发现已发布版本有误时保留旧标签/附件，先核实后选择新的修订版本。

## 6. 发布后核验

读取公开 Release 与 tag 的真实指向；下载到新验证目录，比较本地摘要、服务器 digest 与下载文件。访问真实 README、图片、下载入口和更新清单。HTTP 200 不是最终用户安装成功。记录版本、准确源 commit/tag、CI、附件摘要、链接、测试范围、回滚办法和发布时间。

Git 网络失败时优先正常诊断；Git Data API 兜底须保留文件字节和 mode、核对父提交与根 tree，并以 `force:false` 更新 ref。不能把一次网络错误当作修改系统代理或安全设置的依据。

## 发布与安装独立

GitHub 发布不等于 App Store 或其他商店上架。本项目不购买证书或推广，不安装后台 watcher；真实账号认证、平台条款和付费操作需使用者按平台要求处理。v1 只支持公开 Release 下载，私有仓库需另行设计认证。
