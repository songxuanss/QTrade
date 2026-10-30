# QTrade 技术架构建议

## 1. 架构原则

1. **模块化单体优先**：一期采用一个 FastAPI 后端 + 明确模块边界，避免过早微服务化。
2. **引擎可替换**：QTrade 不直接绑定任何回测 / 实盘引擎，通过适配层调用。
3. **数据不可变**：原始数据、回测输入、代码版本、参数与结果分开存储并可追溯。
4. **实时与批处理分层**：行情热路径只做采集、校验、分发和轻量计算；重计算进入任务队列。
5. **安全默认**：密钥、权限、风控、审计从第一天进入主链路，而不是后补。

## 2. 推荐总体架构

```text
                     ┌────────────────────────────┐
                     │ Web / Desktop Shell        │
                     │ Next.js + TypeScript       │
                     │ Lightweight Charts + Grid  │
                     └─────────────┬──────────────┘
                                   │ HTTPS / WebSocket
                     ┌─────────────▼──────────────┐
                     │ BFF / API Gateway          │
                     │ Session、CSRF、限流、路由    │
                     └─────────────┬──────────────┘
                                   │
        ┌──────────────────────────┼──────────────────────────┐
        │                          │                          │
┌───────▼────────┐        ┌────────▼────────┐        ┌────────▼────────┐
│ Identity/RBAC  │        │ Core API        │        │ Streaming API   │
│ SSO OIDC       │        │ FastAPI modules │        │ WebSocket/SSE   │
└───────┬────────┘        └────────┬────────┘        └────────┬────────┘
        │                          │                          │
        │                  ┌───────▼────────┐          ┌───────▼────────┐
        │                  │ PostgreSQL     │          │ Redis Streams  │
        │                  │ domain state   │          │ / NATS         │
        │                  └───────┬────────┘          └───────┬────────┘
        │                          │                          │
        │                  ┌───────▼────────┐          ┌───────▼────────┐
        │                  │ TimescaleDB /  │          │ Realtime worker │
        │                  │ ClickHouse     │          │ indicator/risk  │
        │                  │ market data    │          └───────┬────────┘
        │                  └───────┬────────┘                  │
        │                          │                           │
        │                  ┌───────▼────────┐           ┌───────▼────────┐
        │                  │ Object Storage │           │ Broker/Exchange │
        │                  │ S3/MinIO       │           │ Adapter         │
        │                  │ Parquet/raw    │           └───────┬────────┘
        │                  └───────┬────────┘                   │
        │                          │                            │
        │                  ┌───────▼────────┐           ┌───────▼────────┐
        │                  │ Job Scheduler  │           │ Trading Engine │
        │                  │ Dagster/Celery │           │ LEAN/Nautilus  │
        │                  └────────────────┘           └────────────────┘
```

## 3. 推荐技术栈

### 3.1 前端

| 能力 | 建议 |
| --- | --- |
| 框架 | Next.js / React / TypeScript |
| 组件 | shadcn/ui、Radix UI、Tailwind CSS |
| 图表 | TradingView Lightweight Charts、ECharts、Plotly 可组合 |
| 表格 | AG Grid / TanStack Table；一期可用 TanStack Table 起步 |
| 状态 | TanStack Query + Zustand |
| 实时 | WebSocket，必要时 SSE 降级 |
| 表单 | React Hook Form + Zod |
| 测试 | Playwright + Vitest |

TradingView Lightweight Charts 是 Apache 2.0 许可，适合 K 线与价格轴。若需要更完整的画图和截面分析，可叠加 ECharts / Plotly。

### 3.2 后端

| 能力 | 建议 | 说明 |
| --- | --- | --- |
| API | FastAPI + Python 3.12 | 与 SSO、量化生态一致 |
| 领域数据 | PostgreSQL 16 | 用户映射、策略、订单、告警、审计、配置 |
| 时序数据 | TimescaleDB 起步；ClickHouse 作为扩展目标 | 一期降低运维复杂度，二期按数据量演进 |
| 原始数据 | S3 / MinIO + Parquet | 不可变、可回放、可被 DuckDB / Polars 读取 |
| 缓存 | Redis | 会话缓存、行情快照、限流、分布式锁 |
| 消息 | Redis Streams 起步，Redpanda / Kafka 演进 | 一期避免 Kafka 运维成本 |
| 任务 | Celery / Arq 起步，Dagster 演进 | 数据采集、回测、报告可调度 |
| 研究 | JupyterHub、DuckDB、Polars、pandas | 独立研究环境，不污染生产执行 |

### 3.3 交易与回测引擎

#### 方案 A：QuantConnect LEAN

**优点**

- Apache 2.0，商业友好。
- 事件驱动、专业级建模，支持多资产与多数据源。
- Python / C# 策略支持，社区与文档成熟。
- 已覆盖较多 broker / market data 场景。

**缺点**

- 系统较重，C# 生态与 Python Web 服务集成需要适配层。
- 云端能力与本地开源能力需要仔细区分。
- 对深度定制撮合 / 高频 tick 场景仍需评估。

**适合**：需要多资产、多市场、宽松许可证、快速获得成熟回测和实盘框架。

#### 方案 B：NautilusTrader

**优点**

- Rust 核心 + Python 策略 API，性能与开发效率兼顾。
- 纳秒级事件驱动，回测与实盘语义一致。
- Parquet catalog、确定性回放、订单簿建模能力强。
- 适合 crypto、多交易所、高频或准高频场景。

**缺点**

- LGPL-3.0，商业分发与链接方式需要许可证审查。
- 适配器和数据生态需要投入更多工程工作。
- 对传统券商 / 多资产支持需逐项验证。

**适合**：crypto-first、tick 级研究、低延迟执行、研究-实盘一致性优先。

#### 方案 C：Freqtrade

**优点**：成熟 crypto bot、内置回测、hyperopt、FreqUI、Telegram。

**缺点**：GPL-3.0、偏 crypto 单一场景、平台化与多资产扩展受限。

**结论**：不建议作为 QTrade 通用底座，可作为 crypto 策略参考。

#### 方案 D：Backtrader / vectorbt / Qlib

- Backtrader：教学和小规模策略可用，社区维护与工程化不足。
- vectorbt：适合大规模参数扫描，但社区版有 Commons Clause，商业限制需审查。
- Qlib：适合 AI 因子研究，不是完整实盘执行系统。

### 3.4 引擎选择建议

| 场景 | 推荐 |
| --- | --- |
| 股票 / 期货 / 多资产优先，商业许可保守 | LEAN |
| Crypto / tick / 低延迟优先 | NautilusTrader |
| 一期尚未确定市场 | 先做 2 周 PoC，以 LEAN 为默认候选 |

无论选择哪个引擎，都必须建立：

```text
TradingEngineAdapter
  - validate_strategy()
  - prepare_data()
  - run_backtest()
  - stream_live()
  - submit_order()
  - cancel_order()
  - reconcile()
```

这样 QTrade 的策略、数据、UI、风控不与底层引擎强耦合。

## 4. 核心数据模型

| 领域 | 主要实体 |
| --- | --- |
| Identity | user_identity、workspace、membership、role、permission |
| Instrument | instrument、exchange、calendar、symbol_mapping、contract_spec |
| MarketData | market_data_job、data_batch、ohlcv、trade、book_snapshot、data_quality_issue |
| Strategy | strategy、strategy_version、parameter_set、strategy_state_transition |
| Research | experiment、backtest_run、metric_snapshot、artifact |
| Execution | broker_account、api_credential、order、execution、position、reconciliation_snapshot |
| Risk | risk_limit、risk_event、pretrade_check_result、kill_switch_event |
| Alert | alert_rule、alert_event、notification_attempt、alert_ack |
| Audit | actor_action、security_event、config_change |
| Ops | service_health、task_run、trace_link |

## 5. 关键数据流

### 5.1 历史数据采集

```text
Vendor API / CSV / Websocket
  -> ingestion worker
  -> schema validation / normalization
  -> raw immutable storage
  -> quality checks
  -> TimescaleDB / ClickHouse
  -> data catalog and lineage
```

### 5.2 实时行情

```text
Exchange / Vendor stream
  -> connector
  -> heartbeat + sequence check
  -> Redis Streams / NATS
  -> realtime indicator worker
  -> WebSocket UI
  -> alert evaluator
  -> optional live strategy
```

### 5.3 回测

```text
User creates experiment
  -> freeze strategy commit + dataset version + parameters
  -> engine adapter builds deterministic run
  -> sandbox worker executes
  -> metrics / trades / equity curves stored
  -> report and comparison UI
```

### 5.4 实盘

```text
Strategy signal
  -> pretrade risk checks
  -> idempotent order request
  -> broker/exchange adapter
  -> order/execution events
  -> portfolio update
  -> reconciliation
  -> alerts and audit
```

## 6. 一期部署形态

一期建议单环境可复制的 Docker Compose：

```text
qtrade-web
qtrade-api
qtrade-worker
qtrade-realtime
postgres / timescaledb
redis
minio
optional: engine runner
external: ../sso
```

生产再演进为 Kubernetes 或托管云。第一阶段优先保证备份、迁移、观测与回滚，而不是复杂编排。

