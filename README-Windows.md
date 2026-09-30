# ATS Portal：Windows 网站部署

本包部署网站前端和后端。前端由后端服务直接提供，无需 Node.js、Docker、WSL 或单独的前端构建。现有界面、动态模式筛选、问题分类、轮次结果总览、TX 红色加粗、连续日志阅读以及接收接口均保留。

## 第一次安装

1. 使用 Windows 10/11，安装 [Python 官方 Windows 版本](https://www.python.org/downloads/windows/)，建议普通 64 位 Python 3.12 或 3.13（最低 3.10）。安装时启用 Python launcher 或勾选 **Add python.exe to PATH**。
2. 将完整 ZIP 解压到当前用户可写的本地目录，例如 `D:\Apps\ats-portal`。先解压再运行，不能在 ZIP 预览窗口中运行。
3. 双击 `install.cmd`。第一次需要联网下载 Python 依赖；看到 **Installation complete** 后关闭安装窗口。
4. 双击 `start.cmd`，等待 **Uvicorn running on ...**，浏览器打开 **http://127.0.0.1:8080**。

命令行同样可以启动。打开 PowerShell：

```powershell
cd D:\Apps\ats-portal
.\install.cmd
.\start.cmd
```

保持启动窗口打开。按 **Ctrl+C** 停止；重启电脑后，再双击 `start.cmd` 即可。此包默认前台运行，未自动注册开机服务。安装和运行通常不需要管理员权限。

## 数据存放在哪里

按 **Win+R**，输入 `%LOCALAPPDATA%\ATS-Portal` 后回车。

| 路径 | 内容 |
| --- | --- |
| `%LOCALAPPDATA%\ATS-Portal\data\portal.sqlite3` | 测试记录、结果和问题索引 |
| `%LOCALAPPDATA%\ATS-Portal\data\artifacts\` | 已接收的原始日志与报告 |
| `%LOCALAPPDATA%\ATS-Portal\config\server.json` | 网站配置 |
| `%LOCALAPPDATA%\ATS-Portal\config\token` | 上传凭据；不要公开 |
| 程序目录中的 `config\rules.json` | 可扩展的问题分类规则 |

程序目录与业务数据目录分开。重复执行 `install.cmd` 保留现有配置、凭据和日志。不同 Windows 登录用户有各自的默认目录；请使用同一用户安装和启动。

## 修改端口、数据目录和访问范围

停止服务，用记事本打开 `server.json`，按需修改后保存，再运行 `start.cmd`。该文件为严格 JSON，不能写注释；路径推荐使用 `/`，例如 `D:/ATSData`，反斜杠要写成 `D:\\ATSData`。

| 配置项 | 默认值 | 用途 |
| --- | --- | --- |
| `host` | `127.0.0.1` | 监听地址；只允许本机访问 |
| `port` | `8080` | 网站端口，1–65535 |
| `data_dir` | 当前用户的 ATS-Portal/data | 数据库和原始文件目录 |
| `token_file` | 当前用户的 ATS-Portal/config/token | 上传凭据文件 |
| `timezone` | `Asia/Shanghai` | 测试记录日期使用的时区 |
| `allowed_hosts` | localhost、127.0.0.1、[::1] | 允许用于访问网站的主机名/IP，数组格式 |
| `read_auth` | `false` | 改为 true 后浏览数据也需要凭据 |
| `max_file_mb` / `max_run_mb` | 512 / 2048 | 单文件/单次运行总大小上限 |
| `rules_path` | `null` | null 使用程序附带的规则；也可指定独立规则文件 |
| `ssl_certfile` / `ssl_keyfile` | `null` | 可选 HTTPS 证书和私钥路径，必须成对设置 |

如需同一局域网里的电脑访问网站，假设网站这台 Windows 的 IP 是 `192.168.1.20`，修改以下字段，其他字段保留：

```json
{
  "host": "0.0.0.0",
  "port": 8080,
  "allowed_hosts": ["localhost", "127.0.0.1", "[::1]", "192.168.1.20"],
  "read_auth": true
}
```

**上面只是需要修改的字段，不能用它覆盖整个配置文件。** 根据实际 IP 填写；`0.0.0.0` 是监听地址，不是浏览器访问地址。

在 Windows 防火墙中允许该服务的 TCP 8080 入站，范围限于需要访问的专用局域网。其他电脑访问 `http://192.168.1.20:8080`。HTTP 本身不加密；需要加密时配置自己的 HTTPS 证书/私钥，或由反向代理提供 HTTPS。包内不自动改防火墙、不生成证书。

环境变量可以覆盖配置：`ATS_PORTAL_HOST`、`ATS_PORTAL_PORT`、`ATS_PORTAL_DATA_DIR`、`ATS_PORTAL_TOKEN_FILE`、`ATS_PORTAL_TOKEN`、`ATS_PORTAL_TIMEZONE`、`ATS_PORTAL_ALLOWED_HOSTS`（逗号分隔）、`ATS_PORTAL_READ_AUTH`、`ATS_PORTAL_MAX_FILE_MB`、`ATS_PORTAL_MAX_RUN_MB`、`ATS_PORTAL_RULES`。环境变量优先于 JSON；需要排查配置时检查是否存在旧环境变量。

设置 `ATS_PORTAL_CONFIG_DIR` 可改变整个配置目录。也可以在项目目录中显式检查或启动一个配置：

```powershell
.\.venv\Scripts\python.exe -m ats_portal.launcher check --config "D:\Portal Config\server.json"
.\start.cmd --config "D:\Portal Config\server.json"
```

`check` 校验配置、时区、规则和数据库；会创建尚不存在的数据库，不会新增测试记录。修改 `data_dir` 不会自动搬运旧数据，应停服复制整个数据目录后再修改。不要将多个网站进程同时指向同一数据目录。

## 保留 Ubuntu 上的历史记录（可选）

如果先从空网站开始，无需执行本节。需要迁移历史记录时：

1. 停止 Ubuntu 和 Windows 上的网站服务。
2. 备份 Ubuntu 的 `~/.local/share/ats-portal` 和 `~/.config/ats-portal`。若之前自定义过路径，以实际配置为准。
3. 把 Ubuntu **整个数据目录**复制到 Windows 的目标数据目录。保留 `portal.sqlite3`、若存在的 `portal.sqlite3-wal`/`portal.sqlite3-shm` 和 `artifacts`；不要只复制数据库而漏掉原始日志。不要直接合并两个已有数据集，先备份 Windows 数据。
4. 如需沿用原上传凭据，停服后把原来的 `token` 复制到 Windows 配置目录覆盖新生成的 token。自定义过分类规则时，也应复制规则文件并设置 `rules_path`。
5. 检查 Windows `server.json` 中的本机路径，运行 `start.cmd`，核对原有记录及原始日志。

不要复制 Ubuntu 的 `.venv`、`portal.env` 或 systemd 配置来代替 Windows 安装。若旧归档包含 Windows 保留文件名、含 `~` 的文件名或仅大小写不同的运行 ID/文件名，须先整理后迁移；常见的 `ats-...` 编号和 `.log`/`result.json` 不受影响。Windows 归档拒绝 `~` 是为避免短文件名别名覆盖原件。

## 更新与备份

更新前停止服务，备份数据目录、配置目录以及自定义规则。把新程序解压到新的可写目录，重新运行 `install.cmd`，然后 `start.cmd`。默认配置和数据仍从当前用户的固定位置读取，安装不会重置它们。

如果更换 Python 版本或移动过程序目录，原虚拟环境可能不可用：停止服务，只删除程序目录里的 `.venv` 后重跑 `install.cmd`。不要删除 `%LOCALAPPDATA%\ATS-Portal`。

## 常见问题

- **找不到 Python / 打开 Microsoft Store**：安装 Python 并启用 launcher 或 PATH；关闭并重开终端。需要指定已安装的 Python 时，在 PowerShell 执行 `$env:ATS_PORTAL_PYTHON = 'C:\完整路径\python.exe'` 后执行 `.\install.cmd`。
- **依赖下载失败**：检查当前电脑网络和 pip 代理配置，再运行 `install.cmd`。失败不会清空已有日志。
- **权限错误**：解压到自己的可写目录，例如 `D:\Apps` 或用户目录，不要放在只读目录；不要靠复制 Linux 的 `.venv` 解决。
- **端口被占用 / WinError 10048**：关闭已有网站进程，或修改 `server.json` 的 `port` 后重启。
- **本机能开，局域网打不开**：检查 host、实际 IP、allowed_hosts、Windows 防火墙和网络连通性；不要把 127.0.0.1 当成另一台电脑的地址。
- **Invalid host header**：把浏览器实际使用的 IP/主机名加入 allowed_hosts，不要填写 URL 或端口。
- **配置文件/凭据错误**：看启动窗口显示的文件路径，修正 JSON 或恢复原 token。安装脚本不会悄悄更换已使用的凭据。

完整网站和 API 已在 Linux 工作区进行自动化回归；Windows CMD、原生文件系统及防火墙未在 Windows 实机执行。开发者可在 Windows 安装开发依赖后运行网站侧测试：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q tests/test_portal.py tests/test_deployment.py
```

`tests/test_sender.py` 与 `tests/test_install.py` 属于原 Ubuntu 工具/安装测试，不在本次 Windows 网站部署的范围内。网站接收协议见 `docs/api.md`。
