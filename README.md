# 湖表工坊 · 湖表分区演化与小文件归并平台

面向湖表目录登记、表结构演进、分区规划与演化、小文件归并、快照与时间旅行、入湖水位推进、压实调度、统计画像、隔离区处置、消费视图发布与存储分层的湖表治理平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8001/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5174/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8001`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 湖表目录 | `lake_catalog` | 湖表条目 | 表编号、表名全称、所属库域 |
| 结构演进 | `table_schema` | 结构版本 | 版本编号、所属湖表、列清单 |
| 分区规划 | `partition_plan` | 分区方案 | 方案编号、所属湖表、分区键 |
| 分区演化 | `partition_evolve` | 演化记录 | 演化编号、所属湖表、原分区键 |
| 小文件归并 | `small_file_merge` | 归并任务 | 归并编号、所属湖表、目标分区 |
| 快照管理 | `snapshot_mgr` | 湖表快照 | 快照编号、所属湖表、快照标识 |
| 时间旅行 | `time_travel` | 回溯查询 | 查询编号、所属湖表、目标快照 |
| 入湖水位 | `ingest_watermark` | 水位记录 | 水位编号、所属湖表、水位字段 |
| 入湖批次 | `ingest_batch` | 入湖批次 | 批次编号、所属湖表、来源系统 |
| 写入作业 | `write_job` | 写入作业 | 作业编号、所属湖表、写入模式 |
| 压实作业 | `compact_job` | 压实作业 | 压实编号、所属湖表、触发条件 |
| 过期策略 | `expire_policy` | 过期策略 | 策略编号、所属湖表、保留快照数 |
| 统计画像 | `stats_profile` | 统计画像 | 画像编号、所属湖表、统计粒度 |
| 列统计 | `column_stat` | 列统计 | 统计编号、所属湖表、列名称 |
| 入湖质量规则 | `quality_rule` | 质量规则 | 规则编号、所属湖表、校验列 |
| 隔离区 | `quarantine_zone` | 隔离记录 | 隔离编号、所属湖表、来源批次 |
| 重建补数 | `rebuild_backfill` | 补数任务 | 补数编号、所属湖表、补数窗口 |
| 存储分层 | `storage_tier` | 分层规则 | 分层编号、所属湖表、热层判定 |
| 保留窗口 | `retention_window` | 保留窗口 | 窗口编号、所属湖表、保留粒度 |
| 消费视图 | `publish_view` | 消费视图 | 视图编号、所属湖表、视图定义 |
| 资源额度计量 | `quota_meter` | 额度读数 | 读数编号、所属湖表、额度类型 |
| 湖表审计 | `lake_audit` | 审计条目 | 条目编号、所属湖表、操作类型 |
| 来源交接 | `source_handover` | 来源交接记录 | 交接编号、旧来源系统、新来源系统、交接时刻 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 来源交接

湖表目录「确认登记」时会建立**表级采集截止点**（`handover_store.ensure_table_cutoff`），
作为来源换班时的归属基线。来源交接记录把旧源在途批次按事件时间拆成窗口归属段，
让旧源在途数据和新源接收数据都能入湖并各自推进采集截止点：

- 窗口按半开区间 `[start, end)` 处理，交接时刻归新源。旧源 08:30~09:30、09:00 交接
  拆成旧源 `[08:30, 09:00)` + 新源 `[09:00, 09:30)`；原批归属始终记在旧源。
- 新源提交 09:00~10:00 时若遇到旧源迟到的半批确认，新源确认生效、旧源半批置为
  `superseded`，并返回 `restart_required`，服务随后自动执行「作业重启」。
- 重启后旧源暂时不可写。值班员可用新源接续凭据（`continuation_token`）代送旧源
  窗口，代送只记 `pending_proof`，须旧源恢复后按原批号补认才推进旧源截止点。
- 同一湖表 + 原批号 + 窗口段只保留一条入湖确认，重复提交只命中已有确认。
- 交接前已确认的窗口仍沿原来源核对；不同湖表即使来源和窗口编号相同也互不影响。

专用接口（注册在通用 `/{module}` 之前）：

```text
GET  /api/source_handover/{handover_id}/detail                交接详情
GET  /api/source_handover/entry/{entry_id}/detail             按登记行取详情
POST /api/source_handover/{handover_id}/receive               接收 / 接续代送
GET  /api/source_handover/{handover_id}/proxy-permission      代送许可
GET  /api/source_handover/{handover_id}/pending-windows       待代送窗口
POST /api/source_handover/{handover_id}/verify                按窗口核对
GET  /api/source_handover/table/{lake_table}/cutoffs          表级 / 按来源截止点
```

接收接口在窗口被占用、许可冲突或凭据无效时返回 `409`，`detail.intervals` 给出需要
旧源补证的确切区间。前端详情页路由：`/source_handover/{handover_id}`。

领域：数据工程
子方向：数仓与数据湖
