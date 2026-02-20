# OpsRamp MCP 实施与验证报告（2026-02-20）

## 目标
基于 OpsRamp 官方 API 文档实现一个 Python FastMCP 服务器，至少覆盖：

1. Dashboard 相关（主要是获取 dashboard）
2. MetricsQL 相关
3. 同时兼容 v2/v3

## 文档调研结论
已基于以下文档完成递归抓取与核对：
- `https://develop.opsramp.com/v3/getting-started`
- `https://develop.opsramp.com/v2`
- `https://develop.opsramp.com/v3/api/dashboards`
- `https://develop.opsramp.com/v3/api/metricsql`
- `https://develop.opsramp.com/v2/api/metrics`
- `https://develop.opsramp.com/v2/api/reporting-apps`
- `https://develop.opsramp.com/v2/api/auth`

关键确认：
- OAuth: `POST /tenancy/auth/oauth/token`（client_credentials）
- Dashboard(v3):
  - `GET /dashboards/api/v3/collections`
  - `GET /dashboards/api/v3/collections/{id}/dashboards`
  - `GET /dashboards/api/v3/collections/{id}/dashboards/{id}`
- MetricsQL(v3):
  - `GET /metricsql/api/v3/tenants/{tenantId}/metrics`
  - `GET /metricsql/api/v3/tenants/{tenantId}/metrics/labels`
  - `GET /metricsql/api/v3/tenants/{tenantId}/metrics/labels/{label_name}`
  - `POST /metricsql/api/v3/tenants/{clientId}/metrics/data`
- v2 兼容：
  - `GET /api/v2/tenants/{tenantId}/metrics`
  - `GET /api/v2/tenants/{tenantId}/metrics/{metricName}`
  - `GET /api/v2/tenants/{tenantId}/reporting-apps/available/search`

## 代码交付

### 项目结构
- `pyproject.toml`：项目与依赖定义（`mcp[cli]`, `httpx`, `python-dotenv`）
- `src/opsramp_mcp/config.py`：环境变量配置加载
- `src/opsramp_mcp/client.py`：OAuth + v2/v3 API 客户端
- `src/opsramp_mcp/server.py`：FastMCP 工具注册与入口
- `scripts/smoke_test.py`：端到端联调脚本
- `.env.example`、`.env`：配置模板与占位
- `README.md`：使用说明

### MCP 工具（已实现）
- `opsramp_auth_test`
- `opsramp_dashboard_list_collections`
- `opsramp_dashboard_list_dashboards`
- `opsramp_dashboard_get`
- `opsramp_metricsql_query`
- `opsramp_metricsql_labels`
- `opsramp_metricsql_label_values`
- `opsramp_metricsql_push_data`
- `opsramp_v2_list_metrics`
- `opsramp_v2_get_metric`
- `opsramp_v2_list_reporting_apps`

## 实验与联调结果（真实环境）
目标地址：`https://glcp-ccs-qa.api.opsramp.com`

结果摘要（已实际执行）：
- `auth_ok`: `true`
- `dashboard_collections_count`: `61`
- `metricsql_status`: `success`
- `metricsql_labels_count`: `102`
- `v2_total_results`: `0`

说明：
- Dashboard 与 MetricsQL 已经成功从服务器拿到真实响应。
- v2 metrics 列表调用成功返回空结果（`totalResults=0`），属于业务数据为空，不是接口失败。

## 框架设计说明

### 技术选型
- **FastMCP（Python）**：快速定义工具、易于被 MCP 客户端接入
- **httpx AsyncClient**：异步请求，便于后续扩展并发与超时控制
- **python-dotenv**：本地开发环境变量管理

### 设计原则
- **分层**：`config` / `client` / `server` 分离
- **可扩展**：v3 主能力 + v2 兼容工具并存
- **安全性**：token 缓存 + 过期前缓冲刷新；不在工具返回中暴露完整 token
- **实用性**：工具支持 `additional_headers`，适配不同租户/权限头需求

## 后续建议（可选增强）
1. 增加 Dashboard 分析下载工具：
   - `POST /reporting/api/v3/tenants/{tenantId}/dashboard-analyses`
   - `GET /reporting/api/v3/tenants/{tenantId}/dashboard-runs/{runId}/downloads`
2. 增加自动分页器与限流重试（429 backoff）
3. 增加 tenant discovery 工具（若有可用端点）
4. 增加结构化日志与请求追踪 ID
5. 增加单元测试 + contract test（针对关键 endpoint）

## 结论
该 MCP 服务已经满足你提出的核心范围：
- Dashboard 获取能力 ✅
- MetricsQL 相关能力 ✅
- v2/v3 同时支持 ✅
- 已完成真实环境联调并获得服务端响应 ✅
