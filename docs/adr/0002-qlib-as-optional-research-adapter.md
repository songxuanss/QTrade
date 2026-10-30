# ADR 0002：Qlib 仅作为可选研究适配器，不作为一期核心底座

## Status

Accepted

## Context

Microsoft Qlib 是 AI-oriented 量化研究平台，提供：

- 数据表达式与缓存
- Alpha158 / Alpha360 等因子工作流
- ML 模型训练与预测
- 组合构建研究回测
- 实验记录与报告
- Point-in-time 数据支持
- RD-Agent 等自动化研究能力

它是 MIT 许可，商业使用许可本身较友好。

但 Qlib 的定位偏研究平台，不是完整实盘交易系统。它不应替代 QTrade 的：

- 登录与权限
- 数据治理与审计
- 专业 Web UI
- 事件驱动撮合回测
- 券商 / 交易所执行
- 预交易风控
- 订单对账
- kill switch
- 合规审计

## Decision

### Phase 1

不集成 Qlib。

一期优先交付：

```text
SSO -> 数据 -> 分析 -> 告警 -> 策略 -> 回测 -> paper trading -> 受控实盘
```

一期研究能力先用：

- Polars
- DuckDB
- pandas
- 自研 indicator service
- LeanAdapter / MockEngineAdapter

### Phase 2

如果产品明确需要以下能力，则以 `QlibResearchAdapter` 接入：

- AI / ML 因子研究
- Alpha158 / Alpha360 类因子库
- 跨截面选股模型
- A 股或股票多因子研究
- 模型训练、预测、IC / RankIC 分析
- 自动因子挖掘

### 集成方式

Qlib 只运行在 `qtrade-research-runner` 中：

```text
QTrade canonical Parquet
  -> Qlib data converter
  -> Qlib workflow
  -> factor / model / score artifacts
  -> QTrade normalized research report
  -> optional strategy or Lean backtest input
```

禁止：

```text
QTrade API 直接 import qlib
Qlib 直接访问生产库
Qlib 直接实盘下单
Qlib 私有数据格式写入 QTrade 核心领域表
UI 直接暴露 Qlib 内部配置
```

## Consequences

### Positive

- 一期范围可控，不会被研究平台复杂度拖慢。
- Qlib 的 ML 优点可以在 Phase 2 按需利用。
- Qlib 升级不影响主平台。
- Qlib 输出可以与其他研究引擎输出统一比较。

### Negative

- Phase 1 没有内置 Qlib 的成熟因子工作流。
- 如果一期用户核心诉求就是 AI 因子研究，则该决策需要重新评估。
- 需要额外实现 Qlib 数据转换与结果标准化。

## Revisit When

满足任一条件时重新评估：

1. 第一批核心用户主要是 AI / ML 量化研究员。
2. 需要 Alpha158 / Alpha360 作为一期卖点。
3. 需要 A 股跨截面因子建模。
4. 需要 RD-Agent 自动因子挖掘作为产品功能。
5. QTrade 已完成基础回测、风控和数据治理闭环。

