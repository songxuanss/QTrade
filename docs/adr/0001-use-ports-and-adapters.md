# ADR 0001：采用 Ports & Adapters 保证低耦合

## Status

Accepted

## Context

QTrade 需要集成多个外部系统：

- 交易与回测引擎：LEAN、NautilusTrader、未来自研引擎
- 研究系统：Qlib、Polars、DuckDB、未来 ML 平台
- 数据库：PostgreSQL、TimescaleDB、ClickHouse
- 数据源：CCXT、Polygon、Tushare、券商行情
- 券商 / 交易所：paper broker、IBKR、OKX、QMT 等
- 通知：Email、Webhook、Telegram、Slack

如果领域服务直接依赖这些系统，后期会出现：

- 更换引擎需要重写 API 和 UI。
- 外部数据格式污染核心表。
- 研究任务与实盘任务耦合。
- 风控链路被 broker SDK 绕过。
- 无法复现历史回测。

## Decision

QTrade 采用 Hexagonal Architecture / Ports & Adapters：

- 领域服务只依赖 `Port` 接口。
- 外部系统通过独立 Adapter 接入。
- 引擎、研究、数据、券商任务运行在独立 worker/container。
- 所有外部输入输出经过 anti-corruption layer 转成 QTrade canonical model。
- 所有跨边界任务使用版本化 artifact 与 manifest。

## Consequences

优点：

- 引擎、数据源、券商、存储可替换。
- UI 和 API 合同稳定。
- 研究能力可以按需增加 Qlib，不影响一期主链路。
- 实盘安全边界清晰，订单必须经过 QTrade 风控。
- 便于测试，可用 Mock Adapter 替代外部系统。

代价：

- 初期接口设计成本更高。
- 需要维护标准数据契约和结果转换器。
- 需要更严格的代码审查，防止绕过 Port 直接引用外部 SDK。

