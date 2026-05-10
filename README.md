# Customer Support Agent

一个可量化业务价值的客服工单处理 Agent MVP。

它可以完成：

- 工单自动分类：退款、物流、账号、商品、投诉、发票等。
- RAG 向量检索：把客户问题和知识库规则转成向量，用相似度召回处理依据。
- LangGraph 编排：`classify -> retrieve -> draft -> evaluate`。
- LLM 回复增强：配置 API Key 后生成更自然的回复；未配置时自动使用模板兜底。
- CRM/订单/物流上下文接入：当前用本地 JSON 适配器模拟真实系统，后续可替换成真实 API。
- 回复草稿生成：输出客服可直接审核的中文回复。
- 转人工判断：识别投诉、强负面情绪、低置信度和知识库缺口。
- 业务价值量化：计算分类准确率、自动处理率、节省工时和高风险工单数。
- 人工修改反馈：保存客服改写结果，用于持续评估回复质量。

## 启动

先启动业务系统后端，它从 SQLite 查询客户、订单、物流数据：

```bash
uv run uvicorn app.business_api:app --reload --port 8011
```

再启动 Agent API：

```bash
uv run uvicorn app.main:app --reload --port 8010
```

业务后台页面：

```text
http://127.0.0.1:8011/admin
```

转人工队列页面：

```text
http://127.0.0.1:8011/admin/escalations
```

打开：

```text
http://127.0.0.1:8010
```

## API

### 分析单条工单

```bash
curl -X POST http://127.0.0.1:8010/api/tickets/analyze ^
  -H "Content-Type: application/json" ^
  -d "{\"customer_id\":\"C1001\",\"order_id\":\"O90001\",\"message\":\"快递显示签收了但是我没收到，物流也没人联系我。\"}"
```

### 流式分析工单

```bash
curl -N -X POST http://127.0.0.1:8010/api/tickets/analyze/stream ^
  -H "Content-Type: application/json" ^
  -d "{\"customer_id\":\"C1001\",\"order_id\":\"O90001\",\"message\":\"快递显示签收了但是我没收到，物流也没人联系我。\"}"
```

流式接口使用 SSE，事件顺序为：

```text
classified -> retrieved -> reply_source -> reply_delta... -> completed
```

前端页面已经接入该接口，LLM 回复会边生成边显示。

### 样例集评估

```bash
curl -X POST http://127.0.0.1:8010/api/evaluate
```

### 看板数据

```bash
curl http://127.0.0.1:8010/api/dashboard
```

### 查询外部系统上下文

```bash
curl "http://127.0.0.1:8010/api/integrations/context?customer_id=C1007&order_id=O90007"
```

集成配置状态：

```bash
curl http://127.0.0.1:8010/api/config/integrations
```

### 保存人工修改反馈

```bash
curl -X POST http://127.0.0.1:8010/api/feedback/revisions ^
  -H "Content-Type: application/json" ^
  -d "{\"ticket_id\":\"T-DEMO\",\"original_reply\":\"原回复\",\"revised_reply\":\"人工修改后的回复\",\"category\":\"logistics\",\"accepted\":false,\"editor\":\"agent-reviewer\"}"
```

反馈指标：

```bash
curl http://127.0.0.1:8010/api/feedback/metrics
```

## LLM 配置

默认不强依赖 LLM，因此没有 API Key 也能完整演示。需要启用时配置：

```bash
SUPPORT_AGENT_LLM_ENABLED=true
SUPPORT_AGENT_LLM_API_KEY=your-api-key
SUPPORT_AGENT_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
SUPPORT_AGENT_LLM_REPLY_MODEL=qwen-plus
SUPPORT_AGENT_LLM_TIMEOUT_SECONDS=45
```

也兼容 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`DASHSCOPE_API_KEY`、`DASHSCOPE_BASE_URL`。

## LangGraph 流程

```text
classify -> retrieve -> draft -> evaluate
```

- `classify`：读取 CRM、订单、物流上下文，并完成分类、优先级和风险标记。
- `retrieve`：按分类和客户原文检索知识库。
- `retrieve`：使用本地 hashed n-gram embedding 做 RAG 向量召回，返回 Top-K 知识库规则。
- `draft`：优先调用 LLM 生成自然回复，失败或未配置时使用模板。
- `evaluate`：判断是否转人工，并计算节省工时、处理模式和置信度。

## 真实系统 API 配置

`ExternalSystemGateway` 现在已经改为 HTTP API 适配器，不再读取本地 CRM/订单/物流 JSON。配置方式：

```bash
CRM_API_BASE_URL=https://crm.example.com/api
CRM_API_TOKEN=crm-token
CRM_CUSTOMER_PATH_TEMPLATE=/customers/{customer_id}

OMS_API_BASE_URL=https://oms.example.com/api
OMS_API_TOKEN=oms-token
OMS_ORDER_PATH_TEMPLATE=/orders/{order_id}

LOGISTICS_API_BASE_URL=https://logistics.example.com/api
LOGISTICS_API_TOKEN=logistics-token
LOGISTICS_SHIPMENT_PATH_TEMPLATE=/shipments/by-order/{order_id}

EXTERNAL_API_TIMEOUT_SECONDS=8
```

本项目已经内置一个 SQLite 业务系统后端，默认项目级 `.env` 指向：

```bash
CRM_API_BASE_URL=http://127.0.0.1:8011
OMS_API_BASE_URL=http://127.0.0.1:8011
LOGISTICS_API_BASE_URL=http://127.0.0.1:8011
```

该服务使用数据库文件：

```text
data/business.db
```

对应接口：

```text
GET /customers/{customer_id}
GET /orders/{order_id}
GET /shipments/by-order/{order_id}
GET /admin
GET /api/admin/orders
POST /api/admin/orders/{order_id}/process
POST /api/admin/orders/{order_id}/reopen
POST /api/admin/agent-analyses
GET /api/admin/agent-analyses
GET /api/admin/escalations
POST /api/admin/reset-demo
```

业务后台 `GET /admin` 可以查看订单处理状态，并把订单标记为“已处理”。它会更新 `orders.support_status`、`orders.processed_at` 和 `orders.resolution_note`。已产生 Agent 分析记录的订单可以点击“查看回复”，查看 Agent 原始回复、人工最终回复和复核信息。

Agent 每次真实分析工单时会把分析记录保存到 `ticket_analyses` 表，包括分类、优先级、回复来源、回复草稿、是否转人工、自动回写结果和预计节省工时。业务后台会展示最近一次 Agent 分析摘要；`GET /admin/escalations` 会展示所有需要人工复核的工单。

业务后台右上角的“重置演示数据”会调用 `POST /api/admin/reset-demo`，恢复所有订单为待处理，并清空 Agent 分析记录和人工反馈记录，方便反复演示。

默认请求格式：

- CRM：`GET {CRM_API_BASE_URL}{CRM_CUSTOMER_PATH_TEMPLATE}`
- OMS：`GET {OMS_API_BASE_URL}{OMS_ORDER_PATH_TEMPLATE}`
- 物流：`GET {LOGISTICS_API_BASE_URL}{LOGISTICS_SHIPMENT_PATH_TEMPLATE}`
- Token 会以 `Authorization: Bearer <token>` 发送；如果变量里已经写了 `Bearer xxx`，会原样发送。

适配器会自动识别常见字段名。例如 `customer_id/customerId/id`、`order_id/orderId/id`、`tracking_no/trackingNo/waybillNo` 等。

## 当前评估口径

默认假设：

- 人工全流程处理每单平均 5 分钟。
- Agent 生成草稿后人工复核每单平均 1.2 分钟。
- 低风险自动处理每单平均 0.4 分钟。

所以每条工单都会输出 `estimated_minutes_saved`，样例集会汇总 `total_minutes_saved` 和 `auto_handle_rate`。

## 下一步可升级

- 把 `ExternalSystemGateway` 的 JSON 读取替换为真实 CRM、OMS、物流 API。
- 把人工反馈与分类标签一起沉淀为评估集。
- 增加回复质量评分、质检规则和 A/B 测试看板。
