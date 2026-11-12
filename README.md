# 数据分析工作台

面向企业数据分析师与数据产品经理的一站式数据分析工作台。提供指标口径管理、聚合查询、分析实验设计、数据服务发布与应用集成的完整链路。

这是一个前后端分离的管理平台：前端 React + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 React + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
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

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 指标口径管理 | `indicator` | 业务指标 | 指标编号、指标名称、计算公式 |
| 分析数据源 | `datasource` | 数据源连接 | 连接编号、连接名称、数据库类型 |
| 聚合查询配置 | `query` | 查询模板 | 模板编号、模板名称、查询语句 |
| 分析实验管理 | `experiment` | 分析实验 | 实验编号、实验名称、分析数据集 |
| 分析数据集 | `dataset` | 数据集 | 数据集编号、数据集名称、来源表名 |
| 可视化图表配置 | `chart` | 图表配置 | 图表编号、图表名称、图表类型 |
| 分析报告管理 | `report` | 分析报告 | 报告编号、报告标题、关联数据集 |
| 数据服务发布 | `service` | 数据服务 | 服务编号、服务名称、输出数据集 |
| 数据看板 | `dashboard` | 看板面板 | 面板编号、面板名称、布局配置 |
| 分析维度管理 | `dimension` | 分析维度 | 维度编号、维度名称、维度表名 |
| 全局筛选器 | `filter` | 筛选器 | 筛选器编号、筛选器名称、关联字段 |
| 定时分析任务 | `schedule` | 定时任务 | 任务编号、任务名称、分析类型 |
| 数据导出配置 | `export` | 导出模板 | 模板编号、模板名称、数据源绑定 |
| 查询缓存管理 | `cache` | 缓存规则 | 规则编号、缓存目标、缓存时间 |
| 分析模型管理 | `model` | 分析模型 | 模型编号、模型名称、模型类型 |
| 分析操作日志 | `log` | 操作日志 | 日志编号、操作类型、操作对象 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 操作日志审计工作区

审计员需要按「操作对象」把同一业务对象在指标口径、查询模板、数据服务上的动作串起来，
而不是逐条看日志。前端入口是 `/log/audit`（日志列表每行的「串联审计链路」也会跳到这里）。

后端接口（`backend/app/routers/log.py`）：

| 接口 | 说明 |
| --- | --- |
| `GET /api/log/objects` | 当前部门可见的、可聚合为链路的主对象清单 |
| `GET /api/log/workspace/{object_code}` | 按对象串联：主对象 + 关联对象 + 时间线 + 缺失标记 |
| `GET /api/log` | 日志列表，支持 `operator`、`action_type`、`date_from`、`date_to`、`object_code` |
| `GET /api/log/{id}`、`POST /api/log/{id}/actions` | 明细与状态流转，越权时 403/拒绝且不改记录 |

`GET /api/log/workspace/INDI-0002` 会带出 `INDI-0002`（指标口径）、`QUER-0002`（查询模板）、
`SERV-0002`（数据服务）三类对象，时间线包含指标修改、查询模板发布、数据服务停用等动作。

### 脱敏与权限（`backend/app/services/audit_policy.py`）

脱敏与判权只有这一份实现，操作日志与查询模板清单（`/api/query` 列表、明细、导出）
都复用它，避免两个页面对敏感字段各说各话：

- 手机号 `139****5678`、令牌 `tok_****6c`、商户号 `M-****1`；JSON 参数按字段名脱敏，
  非 JSON 文本按正则脱敏。`mask_record` 返回副本，不修改仓库原始行，不会残留明文。
- 部门归属优先于共享只读：`owner`（本部门）看完整记录（敏感参数仍脱敏）；
  `readonly`（共享只读名单内）只看脱敏摘要，查询模板的原始参数直接隐藏；
  其余一律 `denied`，工作区/明细/动作都拒绝。共享只读与归属同时命中时按归属处理。
- 越权请求直接 403，拒绝路径不写任何日志、不改任何记录。
- 审计视角通过请求头 `X-Audit-Dept` / `X-Audit-Role` 传入；角色 `guest` 永远只有只读视图。

### 日志缺失与采集异常

`audit_missing` 记录缺失区间，工作区以 `missing` 标记返回；采集异常日志（`采集状态 != 正常`）
带 `capture_ok=false` 与原因，显示为「采集中断」，耗时等字段保留 `—` 而不是伪造数值。
缺失标记单独展示，不混入动作条数，也不会被复制成一条“已完成”记录。

### 测试

```bash
cd backend && .venv/bin/python -m pytest tests -q
```

覆盖：链路串联、筛选、脱敏（含明文不泄漏）、跨部门 403 且无痕迹、只读不能写、
归属优先于共享只读、缺失日志不丢失不重复、列表与详情一致。

领域：数据工程
子方向：数据分析与数据应用
