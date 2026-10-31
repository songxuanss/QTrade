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
- 公司行为账本在除权日开盘前按持仓应用现金分红、送转与拆并；现金按除权前持仓计算，送转不增加当日可卖数量，拆并同时缩放持仓和可卖数量。
- 配股按冻结策略处理：`subscribe` 用可用现金认购（不足则部分认购、余额放弃），新增股份不在除权日当日可卖；`skip` 明确放弃。认购会生成 `rights_subscription` 成交与订单，便于对账。
- 供应商复权因子会随运行冻结并核对：因子发生变化但当天没有对应公司行为时拒绝运行；混合前/后复权口径拒绝；复权价回放（`vendor_adjusted`）当前显式拒绝并披露，绝不静默把复权价当成交价。
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

### Walk-forward 真实运行（600519.SH 日线，2025 全年）

- 切分：训练 120 天、验证 30 天、步长 30 天，生成 9 个互不重叠的验证窗口
- 批次 `f6311814-a419-4e11-852e-2116f832f865`（API 验收）与 `0efb425f-6325-4687-bd92-ca4303176e6c`（UI 验收）
- 每个验证窗口都是一个通过持久队列执行的独立回测，状态全部 `completed`
- 聚合与各窗口报告逐项对账：验证均值 `-0.22%`、中位 `-0.21%`、窗口复利 `-1.94%`、正收益窗口 `33.33%`、平均回撤 `0.60%`、最差回撤 `1.12%`
- 窗口成交数为 0，因为买入持有策略在 30 天窗口内没有平仓交易；收益来自持仓盯市，不是伪造成交
- 失败/取消窗口单独计数，不进入均值；批次状态会显示 `partial`

证据：

- `.tools/verification-local/review/walk-forward-report.json`
- `.tools/verification-local/review/walk-forward-result.json`
- `.tools/verification-local/review/design/walk-forward-result-1440.png`
- `.tools/verification-local/review/design/walk-forward-result-390.png`

### 报告导出（真实下载）

- 回测报告页提供「下载 CSV」与「下载 HTML / 打印 PDF」：仅对已完成的运行开放，未完成返回冲突。
- Walk-forward 结果区提供同样两个按钮，导出批次聚合与逐窗口结果。
- CSV 为分节格式（`# section: run|metrics|parameters|execution_model|corporate_actions|equity_curve|orders|fills`），字段与不可变报告、manifest 一一对应。
- HTML 是独立可打印文件，包含指标卡、权益曲线 SVG、冻结输入、策略参数、执行假设、公司行为及事件版本、期末持仓、订单与成交，并提供打印样式。
- 真实下载验证：Run `9c33a542-94c7-4879-9ab1-22ee9646947f` 的 CSV 25,429 字节、HTML 11,374 字节，数值与刷新后持久化报告一致；HTML 渲染为 383,469 字节 PDF。Walk-forward 批次（4 窗口）导出 CSV/HTML，聚合与页面数值一致。
- 未登录访问导出接口返回 401；导出响应带 `Content-Disposition: attachment`、`Cache-Control: no-store`、`X-Content-Type-Options: nosniff`。

证据：

- `.tools/verification-local/review/exports/export-report.json`
- `.tools/verification-local/review/exports/walk-forward-export-report.json`
- `.tools/verification-local/review/exports/qtrade-backtest-9c33a542-20241231.csv`
- `.tools/verification-local/review/exports/qtrade-backtest-9c33a542-20241231.html`
- `.tools/verification-local/review/design/backtest-export-print.pdf`
- `.tools/verification-local/review/design/backtest-report-export-1440.png`

### 成员区间与幸存者偏差（真实运行）

- 工作区可登记标的的有效区间 `[生效起, 生效止)`，保存来源、证据和内容版本；重叠区间拒绝保存。
- 回测提交可显式选择 `membership` 模式：数据集内每个标的必须有区间，缺失时直接拒绝并列出标的，不静默忽略。
- 区间与覆盖统计冻结进 manifest：全程覆盖、区间不完整、未知数量，并写入幸存者偏差披露文本。
- 引擎只在成员区间内交易；区间结束的最后一根可用 bar 按收盘价强制平仓（T+1 不适用于退出），订单原因标记 `membership_end`。
- 报告、CSV/HTML 导出都展示区间、版本、覆盖统计和披露文本。
- 真实运行：Run `d6c3a7a2-6a26-425f-9dc5-2dcf0f5853c6`（600519.SH，区间 2025-01-01 至 2025-09-30，100 股买入持有）。2025-09-29 按收盘价 1460.86 强制卖出 100 股，期末持仓 0；期末现金 `999166.5737` 与成交、费用和 2025-06-26 股息 `2767.30` 精确对账。同一提交对无区间标的返回 400。
- 注意：该区间由工作区定义并记录证据，**不是交易所官方历史成分**；披露文本明确说明这一点。

证据：

- `.tools/verification-local/review/membership-report.json`
- `.tools/verification-local/review/membership-result.json`
- `.tools/verification-local/review/design/backtest-membership-report-1440.png`
- `.tools/verification-local/review/design/backtest-membership-report-390.png`
- `.tools/verification-local/review/design/backtest-membership-panel-1440.png`

### 配股与供应商复权因子（验收）

- 配股：`rights_policy` 冻结进 manifest；`subscribe` 按 `floor(持仓/基准股数×配股数)` 与可用现金取小认购，`skip` 保留现金；认购成交原因标记 `rights_subscription`。
- 因子：发布的复权因子随 manifest 冻结（含 `content_version`），用于检查「因子变化是否都有对应公司行为」；无对应事件的变动、混合复权口径、越界标的都会拒绝运行。
- `vendor_adjusted` 复权价回放显式返回 422 并披露「未实现」，不静默替换成交价。
- 真实价格验收（600519.SH 2025 日线，供应商配股数据未接通，事件为验收 fixture）：100 股买入后，2025-07-15 以 100 元/股认购 30 股，成交与订单原因正确，期末现金 `847430.1425`、持仓 130 股，与买入成本、费用和认购款精确对账；两条因子版本冻结在 manifest；人为加入未解释的因子变动被拒绝。
- 部署验收：Run `8e6fe483-6ef6-4582-8b4e-cc0fbc50cd8f` 以 `rights_policy=skip` 完成，manifest 与 CSV/HTML 均披露策略与因子状态；`vendor_adjusted` 请求返回 422。

证据：

- `.tools/verification-local/review/rights-factor-report.json`
- `.tools/verification-local/review/rights-factor-deployed.json`
- `.tools/verification-local/review/design/backtest-rights-factor-1440.png`
- `.tools/verification-local/review/design/backtest-rights-factor-390.png`

## 5. 用户体验改进

- 回测表单按策略版本 schema 渲染参数字段，不再要求用户手写 JSON。
- 策略版本选择显示策略名和版本号，运行历史显示对应策略和数据集，而不是 UUID 片段。
- 运行中每 4 秒刷新；提供“刷新状态”按钮，避免排队状态长期不更新。
- 完成运行可勾选 2–20 个进行对比；保留“对比最近两次完成回测”快捷入口。
- 日线回测显示“收盘后可用、无盘中时段过滤”，不再显示误导性的盘中午休假设。
- 报告指标统一保留两位；显示参数、代码/数据集摘要、引擎和 seed；超过 100 条订单/成交显示截断说明。
- 表单明确展示当前模型边界：未处理逐笔盘口和官方历史成员区间；现金分红、送转/拆并、配股策略、供应商因子冻结核对、T+1、整手、最小价位和成员区间校验已建模。

## 6. 仍不满足的专业能力

以下能力未完成，不能宣称“专业研究/实盘就绪”：

- 供应商未接通配股与复权因子数据；配股策略与因子冻结/核对逻辑已实现，复权价回放未实现并显式拒绝。
- 供应商历史成员区间不可用；当前只能使用工作区定义或供应方区间，非官方区间仍可能低估幸存者偏差。
- 逐笔盘口、订单簿冲击和逐笔成交概率未建模；当前仅有最小价位取整。
- 未实现订单类型/TIF 的完整撮合、涨跌停拒单原因和停牌处理。
- 未实现基准相对指标（alpha/beta/超额/跟踪误差/信息比率）与基准时间对齐。
- Walk-forward 已逐窗口执行并聚合；训练区间不参与参数寻优，当前是冻结参数的滚动样本外评估，不是参数选择流程。
- 回测任务已改为持久队列 + Worker 消费；重启恢复和取消已验证。多 Worker 公平调度、租约心跳和细粒度执行中断仍未实现。
- 已提供 CSV 与可打印 HTML（另存为 PDF）导出；未提供服务端直接生成 PDF 的接口。
- 风险引擎未接入回测/Paper 订单路径，qualification 可被伪造。

## 7. 下一步优先级

1. 供应商配股/复权因子接入与官方历史成员区间。
2. 逐笔/盘口执行模型与订单类型/TIF 完整撮合。
3. 多 Worker 公平调度、租约心跳和执行中断。
4. 供应商历史成员区间接入与官方退市事件。
5. 基准相对指标与 Walk-forward 参数寻优。
6. 风险引擎与 Paper/实盘订单路径统一。
