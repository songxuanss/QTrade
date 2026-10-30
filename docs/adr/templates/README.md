# Phase 0 决策模板与 Open Questions 对照

本目录交付任务 1.6 的记录模板，不代表技术或市场选择已经定版。
以下逐项对应 `openspec/changes/add-qtrade-phase1-mvp/design.md` 的五条 Open Questions：

| Open Question | 决策模板 | 定版所需证据 |
| --- | --- | --- |
| 第一期目标市场与数据供应商 | [目标市场](target-market.md)、[数据供应商](data-provider.md) | 市场范围、数据授权、粒度与供应商 PoC |
| 首个真实 Broker/交易所通道 | [Broker](broker.md) | 通道权限、恢复/对账 PoC、实盘 GA 范围 |
| LEAN 与 NautilusTrader PoC 结果 | [引擎](trading-engine.md) | 隔离执行、结果转换、确定性与失败映射 |
| TimescaleDB 与 ClickHouse 数据基准 | [行情存储](market-data-store.md) | 规模、查询目标与同数据基准报告 |
| A 股或其他受限市场的程序化交易合规边界 | [合规边界](trading-compliance.md) | 适用限制、授权和真实资金接入条件 |

目标市场与数据供应商分别记录，交叉引用同一范围；其余问题各有独立模板。
已接受的 ADR 0001/0002 继续有效。这里不替代任务 4.3 的实际存储基准或
任务 7.8 的引擎 PoC，也不解除任何 Live Runtime 安全前置条件。
