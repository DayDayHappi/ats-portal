# HTTP 协议 v1

同源网页和独立上传工具共享 `/api/v1`。上传接口要求 `Authorization: Bearer <token>`。所有时间必须带时区；日期筛选基于服务端配置时区。读取接口在 `ATS_PORTAL_READ_AUTH=true` 时也需 Bearer 或网页登录产生的会话 cookie。

## 一次归档

1. `POST /api/v1/runs` 登记清单。首次返回 201；同 ID、完全相同清单返回 200，不同清单返回 409。
2. `PUT /api/v1/runs/{run_id}/artifacts/{name}` 发送文件原始字节。大小与 SHA256 必须与清单一致。上传可重试。
3. `POST /api/v1/runs/{run_id}/finalize`：验证文件齐全，解析、分类、原子提交。重复提交不会重复问题记录。少文件返回 409，格式问题返回 422，解析大小超限返回 413。

```json
{
  "schema_version": 1,
  "run_id": "ats-unique-run-001",
  "mode": {"id": "arbitrary-mode", "name": "任意新模式"},
  "started_at": "2026-09-28T10:30:00+08:00",
  "ended_at": null,
  "execution_state": "completed",
  "source": {"host": "ubuntu", "device": "EVB-01"},
  "artifacts": [{"name": "run.log", "kind": "log", "size": 0,
    "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}]
}
```

`execution_state`：completed / aborted / unknown。`kind`：report / log / note / attachment；至多一份 report，可没有报告，此时产生报告不完整问题。模式是数据，不是枚举。结果状态来自实际 `results`，不盲信输入 summary。

Windows 服务端额外遵循其文件系统约束：禁止保留设备名（例如 `NUL.log`、`CON`）、不支持的字符以及以空格/点结尾的文件名；同一清单中的文件名不能仅大小写不同（422）。为防止短文件名别名指向其他原件，Windows 归档还拒绝包含 `~` 的文件名。运行编号不能与已有编号仅大小写不同（409）。普通 `ats-...` 编号、`serial.log`、`run.log` 和 `result.json` 可照常使用。

## 读取

| 方法 / 路径 | 作用 |
| --- | --- |
| GET `/api/health` | 进程健康状态 |
| GET `/api/v1/options` | 已上传模式、问题类别、时区 |
| GET `/api/v1/runs` | `page`, `page_size`（最多100）, `mode`, `date_from`, `date_to`, `status`, `category`, `issues_only`, `q` |
| GET `/api/v1/runs/{id}` | 运行结果、问题、文件接收情况 |
| GET `/api/v1/runs/{id}/artifacts/{name}/lines` | `start` 从1开始，`limit` 最多300；返回下一页行号 |
| GET `/api/v1/runs/{id}/artifacts/{name}/search` | `q` 字面关键词、`start`, `limit` 最多100；返回匹配行和继续搜索位置 |
| GET `/api/v1/runs/{id}/artifacts/{name}/locate` | `at`（`HH:MM:SS[.mmm]`）返回该时刻起首个发送命令行（`TX>`）的行号；无则回退之前最近的行 |
| GET `/api/v1/runs/{id}/artifacts/{name}/download` | 原始字节下载 |
| POST `/api/v1/session` | JSON `{"token":"..."}` 建立本机阅读会话 |

关键词搜索不区分大小写，不执行正则；每批达到匹配上限或扫描约16 MiB字符后返回 continuation。一条超长物理行可超过该批扫描预算，但保留的展示文本受限。页面不会把日志当 HTML 渲染。

错误响应包含 `detail`。401 凭据无效；403 跨站写入；404 不存在；409 状态冲突；413 超限；422 输入或文件校验错误；507 本机磁盘错误。网络/408/429/5xx 可稍后重试；永久错误应修复输入。已登记的 manifest 不可修改，需另建 run_id。
