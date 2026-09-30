# ATS 本机测试归档平台

接收 ATS 执行后的报告与日志，按日期、动态压测模式、测试状态和问题类别浏览；保留原始文件，支持日志搜索、上下文阅读与下载。界面采用 ReportPortal 风格的侧栏、执行列表和日志工作区。

**支持 Ubuntu 和 Windows 网站部署。Windows 请先阅读 [README-Windows.md](README-Windows.md)，双击 `install.cmd` 安装、`start.cmd` 启动。** 下文的 Bash 命令用于 Ubuntu。交付工作区里的验证不等于已安装到你的电脑。首次安装需要联网下载 Python 依赖；正常运行不依赖云服务。安装不会生成演示测试记录。

## 1. 安装和启动

要求：Ubuntu、Python 3.10+（建议系统 Python）、可用的 `python3-venv`。无需 Docker、Node.js 或外部数据库。

解压 ZIP，进入 `ats-portal` 目录：

```bash
bash scripts/install.sh
bash scripts/start.sh
```

若提示缺少 venv，先安装：`sudo apt install python3-venv`。打开 **http://127.0.0.1:8080**。前台运行时保持终端打开，按 Ctrl+C 停止。安装脚本可重复执行，不覆盖已有数据和凭据。

如果出现 `.venv` 的 `Permission denied`，表示当前用户无法写入项目目录或已有虚拟环境，并不等同于缺少依赖。先用 `ls -ld . .venv` 检查（尚未创建 `.venv` 时提示不存在是正常的）。将该独立项目目录的属主和写权限恢复给当前用户后重试；不要用 `sudo bash scripts/install.sh` 代替修复权限。

可选后台服务：先停止前台服务，然后执行 `bash scripts/service.sh install`。此服务随用户登录启动；关机后未登录时是否运行取决于 Ubuntu 的用户服务设置，本项目不会修改系统 linger 设置。状态与日志分别用 `bash scripts/service.sh status`、`bash scripts/service.sh logs` 查看。

## 2. 上传一次已有 ATS 运行

先确保 ATS 完全结束，替换下方路径为真实目录。`--log-dir` 必须指向包含 `serial.log`、`run.log` 的那次运行目录：

```bash
python3 tools/ats_upload.py send \
  --log-dir /你的ATS目录/logs/你的模式/20260928/20260928_103000 \
  --state completed
```

`completed` 表示 ATS 正常走完流程，测试本身可以 FAIL。异常终止用 `--state aborted`；不确定用 `unknown`（默认值），不会误显示为通过。

工具默认只收集运行目录**顶层**的 `.log`/`.txt` 文件，自动查找对应的 `result.json` 和手工问题记录。默认不进入任何子目录，包括 `photos/`、`videos/`、`video_integrity/`；这些目录不参与文件数量统计。未来需要子目录内的文本日志时，给 `send` 或 `run` 增加 `--recursive`（`run` 放在 `--` 前）。递归收集仍受单次100文件上限约束，也不会上传录像等大媒体文件。非标准目录可显式指定：

```bash
python3 tools/ats_upload.py send \
  --log-dir /测试日志目录 \
  --report /对应的/result.json \
  --mode your_new_mode --mode-name "你的新压测模式" \
  --started-at 2026-09-28T10:30:00+08:00 \
  --state completed --device EVB-01 --firmware build-123
```

新 `mode` 上传后会自动出现在筛选器里，无需改前后端代码。模式 ID 应保持稳定；名称可随新运行更新。历史记录保留当时的显示名称。

## 3. 接在原 ATS 命令外层

不修改 ATS 源码。在项目目录执行（请替换 ATS 路径和原命令）：

```bash
python3 /绝对路径/ats-portal/tools/ats_upload.py run \
  --project /绝对路径/你的ATS目录 \
  -- python3 main.py --scenario 你原来的模式
```

`--` 后完整保留你原来的 ATS 命令，包括虚拟环境 Python 路径和参数。工具透传标准输入、显示输出，ATS 结束后从其“运行时间戳 / 日志目录 / 报告目录”输出识别实际文件。ATS 的退出码原样返回：0/1 视为流程完成，其他码视为异常终止。若无法识别路径，会提示使用 `send`；原始日志不会删除。

**上传失败不改变 ATS 的退出码。** 日志复制到本机待发送队列，修复网络、服务或配置后手动重发：

```bash
python3 tools/ats_upload.py retry
```

队列副本一经建立不会随原日志变化，重试使用同一运行 ID，不会重复入库。重复 `send` 检测到源文件/元数据变化时会拒绝；明确要创建另一条记录才用 `--new-run`。成功队列也保留，确认归档和备份后可手动清理对应队列目录；不要编辑其中的文件。

升级后 `retry` 仍重发已有队列的原始清单，新的收集范围只影响新建队列。若此前在收集阶段因文件数量超限而失败，尚未建立队列，更新工具后直接重新 `send` 即可。

## 4. 数据、配置和备份

| 位置 | 内容 |
| --- | --- |
| `~/.local/share/ats-portal/portal.sqlite3` | 运行、结果、问题索引 |
| `~/.local/share/ats-portal/artifacts/` | 校验后的原始文件 |
| `~/.local/share/ats-portal/outbox/` | 待发送及已发送日志副本 |
| `~/.config/ats-portal/portal.env` | 端口、时区、数据目录等启动配置 |
| `~/.config/ats-portal/token` | 自动生成的上传凭据；不要提交或分享 |
| `config/rules.json` | 可扩展问题分类规则 |

默认只监听 `127.0.0.1`。脚本上传必须持有凭据，本机浏览默认无需登录。如希望浏览也验证凭据，在 `portal.env` 设置 `ATS_PORTAL_READ_AUTH=true` 后重启，网页输入本机 token 文件里的值。

修改 `ATS_PORTAL_PORT` 后，发送端增加全局参数 `--server http://127.0.0.1:新端口`（放在 `send/retry/run` 前）。修改服务端 token 位置时，发送端也需指定全局 `--token-file`。配置只在重启后生效。

备份前停止服务，再复制整个 `~/.local/share/ats-portal` 和 `~/.config/ats-portal`，确保数据库与原始日志一致。升级时保留这两个目录，替换代码、重新执行安装并启动。删除代码文件夹不会删除这些持久数据。当前不自动清理历史数据，请关注磁盘余量；队列副本与服务端原件会分别占用空间。

## 5. 当前范围

- 用例结果按轮次汇总，默认优先展示有问题的轮次；直接显示“第几轮 / 第几个测试项 / 第几次重复”需关注，点击后查看具体记录和关联日志。PASS 但关联了问题的记录仍提示，原始判定保持不变。可选择轮次、切换全部记录。
- 测试项以场景、模块和结果名称分组，编号按各场景内首次出现的顺序生成，不是 YAML 配置序号。现有 ATS 报告没有保存配置项 ID/序号，因此同模块、同名称的配置项会合并展示；每条执行记录、重跑记录仍保留。未记录轮次明确单独展示，未上报项不推定为通过。
- 日期归属按运行开始时间换算为配置时区（默认 Asia/Shanghai），跨午夜也只属于一个开始日期。
- PASS/FAIL/ERROR 与问题类别独立：PASS 的报告里出现 `TT ERROR` 仍能进入问题视图。
- 分类来源是 `result.json` 的结果字段、恢复事件及手工问题记录。**当前不自动扫描所有原始日志做分类**；日志支持手动全文搜索。原始报告没有精确日志行引用时，问题入口发起关键词搜索，用户选择实际匹配行。
- 缺失报告、未知状态和异常终止明确展示，不猜测为 PASS。
- 原始日志在同一阅读区连续滚动，靠近底部自动读取后续内容，可向上滚动回看；点击“展开阅读”放大到窗口，按 Esc 或“收起阅读”返回。保留关键词搜索、行号跳转、上下文阅读和原文件下载。搜索匹配列表仍可按段切换。
- 默认单文件上限 512 MiB、单次运行 2 GiB、100 个文件；结构化报告最多 16 MiB、手工问题文件最多 1 MiB。超限会明确拒绝。大日志每次按需读取300行，页面最多保留900行，滚回前文时重新读取；单行展示最多 8192 字符，原始下载不截断。深处读取及搜索仍顺序扫描，大文件可能较慢。日志页和搜索结果会将 `[时间] TX>` 后的发送命令（含 TX 标记）加粗标红，时间戳保持原样。
- 面向本机少量并发，不包含测试调度、用户管理、实时串流、跨机局域网开放、自动保留策略或 Elasticsearch。这些能力可在模块边界上继续扩展。

如浏览器缺少中文字体，可安装 Ubuntu 的 `fonts-noto-cjk` 软件包。

接口、扩展和验证记录见 [docs/api.md](docs/api.md)、[docs/extension.md](docs/extension.md)、[docs/validation.md](docs/validation.md)。
