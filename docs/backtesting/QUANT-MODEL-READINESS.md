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
- 买单先按现金计算可负担数量，再按整手和最低佣金/费用校验；现金不得为负。
- A 股 T+1 可卖数量已建模：当日买入不能当日卖出，下一交易时段持仓才可卖。
- 成交价按 `tick_size` 向不利方向取整（买入向上、卖出向下）。
- 费用支持手续费 bps、最低佣金和卖出印花税；滑点、成交量占比、涨跌停阈值和周期年化频率均冻结在 manifest。
- 公司行为账本在除权日开盘前按持仓应用现金分红、送转与拆并；现金按除权前持仓计算，送转不增加当日可卖数量，拆并同时缩放持仓和可卖数量。配股目前明确拒绝，避免猜测。
- 每条公司行为以 `content_version` 冻结进 manifest；公司行为来源不可用或版本冲突时回测拒绝创建，不静默忽略。
- 日线数据不套用盘中 09:30–15:00 时段；分钟数据仍使用配置的 A 股时段和午休。
- 绩效从初始资金而不是第一个 bar 后权益计算，首期费用/亏损进入最大回撤与年化收益。
- 回测读取时校验结果与 run、manifest、币种、样本数和期末权益一致；manifest seed 与结果 seed 不一致时拒绝报告。

## 4. 真实模型验收

历史数据：

- 数据集 `d06077ae-a021-5571-82b0-11e7d5de3cc6`
- `601288.SH` 日线，2025-01-01 至 2025-12-31，243 个采样点
- 采集 Job `5c9e54ca-7066-469d-8952-a7e13b941792`，consumer `visible`

策略：

- 真实 SMA(5,20) 源码，静态校验通过，版本状态 `validated`
- 运行 ID `6b00d332-9a92-4d45-9ffb-170eb0e781a1`
- manifest `32e381e9-ac80-58a9-a121-9c8dbb8d5040`

结果：

- 12 个成交，12 个订单，全部成交
- T+1、整手、最小价位、最低佣金和卖出印花税已启用
- 累计收益 `2.07%`，最大回撤 `0.55%`，Sharpe `1.89`
- 6 笔已平仓交易，胜率 `66.67%`，盈亏比 `20.71`
- 结果和 manifest 已持久化，UI 报告可直接打开并在刷新后保留

证据：

- `.tools/verification-local/review/real-backtest-report.json`
- `.tools/verification-local/review/real-backtest-result.json`
- `.tools/verification-local/review/design/backtest-report-real-1440.png`
- `.tools/verification-local/review/design/strategy-default-model-1440.png`
- `.tools/verification-local/review/design/backtests-real-1440.png`
- `.tools/verification-local/review/design/strategy-backtest-ux.json`
- `.tools/verification-local/review/design/backtest-compare-selected-1440.png`

### 公司行为样本（600519.SH，2025 全年，买入并持有 100 股）

- 数据集 `6a3a7642-d970-5402-ab44-69e8e7eb08c8`，`600519.SH` 日线
- Run `75e13372-7969-46f4-b27b-bc11c0037335`，状态 `queued → running → completed`
- 跨 2025-06-26（每 10 股派 276.73）和 2025-12-19（每 10 股派 239.57）两个除息日
- 两条事件 `content_version` 冻结在 manifest：
  - `d5317f75861374294b9a4fbb840d5e99d12ee87d885d7bc9fccf48193e5304fc`
  - `e139b737adab01d513b3d050b27f5c20aa2895807d2785535fcd74998a503089`
- 期末现金 `855593.1425`，持仓 100 股，与成交、费用和股息精确对账
- 报告 UI 展示已冻结公司行为表（除权日、类型、基准派息、送转、拆并、事件版本），桌面与手机均无页面级溢出

证据：

- `.tools/verification-local/review/corporate-action-report.json`
- `.tools/verification-local/review/corporate-action-result.json`
- `.tools/verification-local/review/design/backtest-corporate-report-1440.png`
- `.tools/verification-local/review/design/backtest-corporate-report-390.png`

## 5. 用户体验改进

- 回测表单按策略版本 schema 渲染参数字段，不再要求用户手写 JSON。
- 策略版本选择显示策略名和版本号，运行历史显示对应策略和数据集，而不是 UUID 片段。
- 运行中每 4 秒刷新；提供“刷新状态”按钮，避免排队状态长期不更新。
- 完成运行可勾选 2–20 个进行对比；保留“对比最近两次完成回测”快捷入口。
- 日线回测显示“收盘后可用、无盘中时段过滤”，不再显示误导性的盘中午休假设。
- 报告指标统一保留两位；显示参数、代码/数据集摘要、引擎和 seed；超过 100 条订单/成交显示截断说明。
- 表单明确展示当前模型边界：未处理配股、供应商复权因子、历史成分和逐笔盘口；现金分红、送转/拆并、T+1、整手和最小价位已建模。

## 6. 仍不满足的专业能力

以下能力未完成，不能宣称“专业研究/实盘就绪”：

- 配股和供应商复权因子未进入自动账本；现金分红、送转和拆并已入账。
- 历史有效成分、退市证券和幸存者偏差未接入回测。
- 逐笔盘口、订单簿冲击和逐笔成交概率未建模；当前仅有最小价位取整。
- 未实现订单类型/TIF 的完整撮合、涨跌停拒单原因和停牌处理。
- 未实现基准相对指标（alpha/beta/超额/跟踪误差/信息比率）与基准时间对齐。
- Walk-forward 目前只生成切分计划，未逐窗口执行和汇总。
- 回测任务已改为持久队列 + Worker 消费；重启恢复和取消已验证。多 Worker 公平调度、租约心跳和细粒度执行中断仍未实现。
- 没有 HTML/PDF/CSV 导出和报告下载。
- 风险引擎未接入回测/Paper 订单路径，qualification 可被伪造。

## 7. 下一步优先级

1. 配股、复权因子应用和完整公司行为覆盖。
2. 逐笔/盘口执行模型与订单类型/TIF 完整撮合。
3. 多 Worker 公平调度、租约心跳和执行中断。
4. 历史成分/退市数据接入，回测提交显式选择 universe 快照。
5. 基准相对指标、walk-forward 执行和导出。
6. 风险引擎与 Paper/实盘订单路径统一。
