# 量化模型与回测能力评审（2026-10-31）

## 1. 需求复核结论

量化研究主流程需要同时满足“能定义模型、能冻结输入、能真实执行、能解释结果、能比较实验、能识别限制”。此前部署中的回测只创建排队记录，未执行行情回放；本次已打通：

```text
策略源码 + 参数 schema
  → 静态校验与版本冻结
  → 真实行情数据集
  → 隔离 Runner 执行 Python on_bar
  → 事件驱动撮合、费用、滑点、整手与 A 股时段
  → 不可变 result + manifest
  → UI 报告、权益/回撤、订单、成交与实验对比
```

## 2. 模型设计契约

- 策略是一个 Python 类，继承 `qtrade.Strategy`，实现 `on_start` 和 `on_bar`。
- 每个 bar 仅向策略暴露当前 bar 与策略自身累积状态，不把未来 bar 传入。
- 订单通过 `buy` / `sell` 提交；当前事件引擎支持市价单和 day/gtc，其他订单类型明确拒绝，不静默降级。
- 参数通过版本化 JSON schema 定义；整数、数字、字符串、布尔和枚举可渲染为表单字段，默认值写入 manifest。
- 策略源码在静态校验后冻结；回测运行时按 manifest 的 `code_sha256` 从对象存储读取，并与冻结版本比对。
- 随机种子进入 manifest 和结果 determinism；Runner 按 seed 初始化随机模块。

默认模板已从空 `pass` 改为可运行的 SMA 交叉模型：

- `fast` 默认 5，`slow` 默认 20，`quantity` 默认 1000；
- 金叉买入、死叉卖出，策略自带持仓状态；
- 新建策略后无需手写参数 JSON 即可运行一次真实回测。

## 3. 执行语义

- 信号默认延迟 1 根 K 线，在下一根 bar 的 open 撮合。
- 数量按 `lot_size` 向下取整；成交量上限和现金约束后的最终成交仍按整手校验，不再产生 50 股等不可交易零股。
- 买单先按现金和费用计算可负担数量，再减去整手；现金不得为负。
- 日线数据不套用盘中 09:30–15:00 时段；分钟数据仍使用配置的 A 股时段和午休。
- 费用、滑点、成交量占比、涨跌停阈值和周期年化频率均冻结在 manifest。
- 绩效从初始资金而不是第一个 bar 后权益计算，首期费用/亏损进入最大回撤与年化收益。
- 回测读取时校验结果与 run、manifest、币种、样本数和期末权益一致；manifest seed 与结果 seed 不一致时拒绝报告。

## 4. 真实模型验收

历史数据：

- 数据集 `fa9e45e9-d0b1-59dc-9282-a7fa2e6294d8`
- `601288.SH` 日线，2025-01-01 至 2025-12-31，243 个采样点
- 采集 Job `5af2d6f4-4312-4977-b026-77fd1a55fdba`，consumer `visible`

策略：

- 真实 SMA(5,20) 源码，静态校验通过，版本状态 `validated`
- 运行 ID `51fff763-c497-41da-a224-42035e043370`
- manifest `fd4d5a28-c2a1-5e06-9472-5fc4fecd05ac`

结果：

- 12 个成交，12 个订单，全部成交
- 累计收益 `2.21%`，最大回撤 `0.54%`，Sharpe `2.02`
- 6 个已平仓交易，胜率 `83.33%`
- 结果和 manifest 已持久化，UI 报告可直接打开并在刷新后保留

证据：

- `.tools/verification-local/review/real-backtest-report.json`
- `.tools/verification-local/review/real-backtest-result.json`
- `.tools/verification-local/review/design/backtest-report-real-1440.png`
- `.tools/verification-local/review/design/strategy-default-model-1440.png`
- `.tools/verification-local/review/design/backtests-real-1440.png`
- `.tools/verification-local/review/design/strategy-backtest-ux.json`
- `.tools/verification-local/review/design/backtest-compare-selected-1440.png`

## 5. 用户体验改进

- 回测表单按策略版本 schema 渲染参数字段，不再要求用户手写 JSON。
- 策略版本选择显示策略名和版本号，运行历史显示对应策略和数据集，而不是 UUID 片段。
- 运行中每 4 秒刷新；提供“刷新状态”按钮，避免排队状态长期不更新。
- 完成运行可勾选 2–20 个进行对比；保留“对比最近两次完成回测”快捷入口。
- 日线回测显示“收盘后可用、无盘中时段过滤”，不再显示误导性的盘中午休假设。
- 报告指标统一保留两位；显示参数、代码/数据集摘要、引擎和 seed；超过 100 条订单/成交显示截断说明。
- 表单明确展示当前模型边界：未处理复权/分红送转、T+1、历史成分和逐笔盘口。

## 6. 仍不满足的专业能力

以下能力未完成，不能宣称“专业研究/实盘就绪”：

- 公司行为、分红现金、拆并股、配股与复权因子未进入回测账本。
- A 股 T+1 可卖数量未建模。
- 历史有效成分、退市证券和幸存者偏差未接入回测。
- tick size、逐笔盘口、订单簿冲击、逐笔成交概率未建模。
- 未实现订单类型/TIF 的完整撮合、涨跌停拒单原因和停牌处理。
- 未实现基准相对指标（alpha/beta/超额/跟踪误差/信息比率）与基准时间对齐。
- Walk-forward 目前只生成切分计划，未逐窗口执行和汇总。
- 回测任务仍是本地同步执行；没有持久化队列、Worker 消费、重启恢复和细粒度取消。
- 没有 HTML/PDF/CSV 导出和报告下载。
- 风险引擎未接入回测/Paper 订单路径，qualification 可被伪造。

## 7. 下一步优先级

1. 复权与公司行为账本（含分红现金和拆并股）。
2. T+1 可用持仓、逐笔/盘口执行模型。
3. 持久化回测队列 + Worker + Runner 生命周期。
4. 历史成分/退市数据接入，回测提交显式选择 universe 快照。
5. 基准相对指标、walk-forward 执行和导出。
6. 风险引擎与 Paper/实盘订单路径统一。
