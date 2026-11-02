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
- 订单通过 `buy` / `sell` 提交，支持 `market`、`limit`、`stop`、`stop_limit` 与 `day` / `gtc` / `ioc` / `fok`。限价按开盘可成交价或限价撮合，止损由最高/最低价触发；IOC 成交后撤销剩余，FOK 不可全成则整单撤销，day 单在下一交易时段前撤销，GTC 持续有效。非法组合明确拒绝，不静默降级。
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
- 费用支持手续费 bps、最低佣金和卖出印花税；滑点、成交量占比、涨跌停阈值和周期年化频率均冻结在 manifest。停牌 bar 仍用于估值但不允许成交，GTC 订单在停牌期间保留等待复牌。
- 公司行为账本在除权日开盘前按持仓应用现金分红、送转与拆并；现金按除权前持仓计算，送转不增加当日可卖数量，拆并同时缩放持仓和可卖数量。
- 配股按冻结策略处理：`subscribe` 用可用现金认购（不足则部分认购、余额放弃），新增股份不在除权日当日可卖；`skip` 明确放弃。认购会生成 `rights_subscription` 成交与订单，便于对账。
- 供应商复权因子随运行冻结并核对：因子变化必须有对应公司行为，混合口径或越界标的拒绝。因子已接通真实供应商（由 MaiRui 前复权/不复权收盘价推导），不再是 fixture。
- `vendor_adjusted` 模式按 `factor(t)/factor(start)` 缩放执行 bar 的 OHLC，并**禁用分红/送转/拆并/配股入账**以避免重复计入；资金以复权价计价，报告与导出明确披露价格口径。因子缺失时拒绝运行，不静默退化。
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

### 多策略选股与组合回测（真实 Top 5 + 等权基准）

本节回答“策略是否可灵活定制、数据是否支撑选股、回测结果是否与行业共识一致”。

选股与数据：

- 冻结股票池：`A股-指数成分-沪市-上证央企` 快照 `bbe141c5-ed97-4224-b76e-efadcd553ec2`，50 个成员；50 只全部具备 53 周已发布单标的周度数据集。
- 选股数据集合成：按最近 4 周平均成交额排序，取前 20 只有连续覆盖的成员；区间 `2025-10-30T16:00Z` 至 `2026-10-30T16:00Z`（北京时间 2025-10-31 至 2026-10-30）。
- 数据集 `4ad3c536-681b-57d3-b2f1-7fc224093106`：20 只、242 根/只、合计 4,840 根，质量 `{valid: 4840}`，`raw_record_count=6120`。
- 合成规则会排除多标的合成数据集作为来源，并按快照/区间/选中标的生成确定性 identity；重复合成返回同一数据集，已验证幂等。
- 未入选的 30 只没有进入数据集，因此本验收是“Top 20 流动性成员”而非全 50 只组合；披露写入了数据集只读摘要。

策略（均通过静态校验，参数可在 UI/API 调整后重新冻结版本）：

| 策略 | 经济含义 | 参数 | 版本 |
| --- | --- | --- | --- |
| 中期动量 60-5 | 60 日收益，跳过最近 5 日 | `lookback=60, skip=5` | `b4512295-…` v4 |
| 短期反转 5 日 | 买入 5 日跌幅最大的 5 只 | `lookback=5` | `c10274c9-…` v4 |
| 低波动 60 日 | 60 日已实现波动率最低的 5 只 | `lookback=60` | `bf2ca7e6-…` v4 |
| 趋势 20/60 | 20 日均线高于 60 日，按乖离率排序 | `fast=20, slow=60` | `2ccb920a-…` v4 |
| 风险调整动量 60-5 | 60 日动量 / 同期波动率 | `lookback=60, skip=5` | `bf02b4a7-…` v4 |
| 等权 20 只基准 | 一次买入全池并持有 | — | `2e38c4de-…` v4 |

统一执行输入：初始资金 1,000,000 CNY、`cost.v1`、`Asia/Shanghai`、未复权价 + 公司行为账本、配股 `skip`、整手 100、手续费 3bps（最低 5 元）、卖出印花税 5bps、滑点 5bps、T+1、涨跌停 10%、年化 252。

真实运行（全部 `completed`，并转为 `backtested`）：

| 策略 | Run | 总收益 | 年化波动 | 最大回撤 | Sharpe | 胜率 | 换手 | 订单/成交 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 中期动量 | `71f016b4-f77f-4df9-92e0-e3d67844f887` | +0.40% | 16.36% | 17.00% | 0.11 | 44.4% | 6.03 | 41/41 |
| 短期反转 | `8863b32a-7624-425a-b1ee-2c2e44706f8b` | -24.48% | 16.62% | 25.27% | -1.67 | 36.4% | 13.73 | 93/93 |
| 低波动 | `fb912719-bb7f-418f-9dc6-e86b42d3b94f` | +6.55% | 9.12% | 6.93% | 0.77 | 71.4% | 2.88 | 19/19 |
| 趋势 20/60 | `c7f7c0cd-f351-495b-a456-3b2970c40e26` | -19.77% | 19.17% | 21.92% | -1.10 | 28.6% | 6.84 | 51/47 |
| 风险调整动量 | `f942e768-1b5b-4775-922c-183a0b6184de` | +7.87% | 16.92% | 14.98% | 0.55 | 52.4% | 6.96 | 47/47 |
| 等权基准 | `a607b8c3-0acf-44ce-b4f0-29fa711c8b22` | +7.17% | 12.46% | 8.80% | 0.64 | — | 0.88 | 20/20 |

横向比对与行业共识核对：

- `backtests/compare` 返回 `comparable=true`、`warnings=[]`，唯一输入差异为 `strategy_version_id`，说明数据、区间、成本、执行与参数口径一致。
- 低波动策略的波动率（9.12%）和最大回撤（6.93%）在 5 个主动策略中最低，符合低波动异象的行业共识。
- 短期反转的换手最高（13.73×），且在高流动性大盘蓝筹池中亏损最大；这与“反转因子在小盘/高换手样本更有效、蓝筹池中交易成本侵蚀收益”的共识一致。
- 趋势与反转在 2025-10 至 2026-10 的震荡蓝筹年份为负收益，等权基准为正；动量仅微正。结果方向可解释，不宣称样本外可直接复制。
- 等权基准的换手仅 0.88×，作为被动对照合理。

参数灵活性与确定性（同一策略版本、不同参数）：

- 同一动量 v4 改为 `lookback=40, skip=5, top_n=3, position_value=280000, rebalance_every=10, lot_size=100`。
- Run `4fd6606a-120e-45c0-85d1-7187e3134531`：总收益 -14.38%、最大回撤 28.32%、Sharpe -0.45、换手 10.58×、39 订单/39 成交；与默认参数的 +0.40% 形成明确差异，证明参数确实进入执行而不是只渲染表单。
- 用完全相同的输入再运行一次 `3127f50f-4278-470e-bff8-3e3ab99211c0`：权益曲线 SHA-256 同为 `7607a846994f…`，全部指标逐项相同，证明同版本同参数可复现。
- 对比页 `backtests/compare` 在桌面与手机渲染 6 条权益路径和指标差异表，显示“输入范围可直接比较”、唯一差异 `strategy_version_id`，无横向溢出。

限制：

- 数据集只有 242 根/只，无法构造 12-1 动量（需要约 252 根），因此使用 60-5 作为中期动量代理；不能把结果直接等同于经典 12-1 组合。
- 池为冻结的上证央企快照，成员按流动性取 20 只；成分区间不是交易所官方历史，存在幸存者偏差披露。
- 无 L2 盘口与真实冲击数据，`impact_bps=0` 的运行是 bar 级近似。
- 低波动正收益、反转负收益是单一年度单一样本的结果，只能证明链路、口径与方向解释，不构成未来收益承诺。

UI 与持久化：

- 桌面 1440×1000 与手机 390×844 均完成真实 SSO 登录、合成面板提交、回测页选择数据集/策略、报告与导出浏览；无页面级横向溢出，控制台无错误。
- 从 UI 新建的动量 v4 回测 Run `fbc031e3-cba3-47ad-8324-2cd8cddf0e13` 使用 1,000,000 初始资金，结果与 API 验收 Run `71f016b4-…` 完全一致（+0.40%、41 订单/41 成交），刷新后报告仍可读取。
- 下载按钮真实产出 CSV 39,104 字节、HTML 29,478 字节；CSV 首行为 `# QTrade backtest export · 71f016b4-…`。

拒绝路径（真实部署）：

- 用仅 2 个成员的股票池合成 20 只 → 400。
- 用 50 个成员、目标 50 只合成 → 400（50×242=12,100 根，超过 Runner 5,000 根预算）。
- 新建并 `validated` 一个没有完成回测的版本，直接转 `backtested` → 403，qualification 必须由精确版本的完成 Run 推导。
- 将成员角色临时降为 `viewer`：合成数据集 → 403；提交回测 → 403；随后恢复 `admin`。角色变更在 `finally` 中还原，数据库最终为 `admin`。

证据：

- `.tools/verification-local/review/strategy-portfolio-report.json`
- `.tools/verification-local/review/portfolio-rejections.json`
- `.tools/verification-local/review/ui-strategy-portfolio/ui-results.json`
- `.tools/verification-local/review/ui-strategy-portfolio/desktop-03-composition-done.png`
- `.tools/verification-local/review/ui-strategy-portfolio/desktop-04b-ui-run-report.png`
- `.tools/verification-local/review/ui-strategy-portfolio/mobile-03-report.png`
- `.tools/verification-local/review/ui-strategy-portfolio/qtrade-backtest-71f016b4-20251030.csv`
- `.tools/verification-local/review/ui-strategy-portfolio/qtrade-backtest-71f016b4-20251030.html`
- `.tools/verification-local/review/ui-strategy-portfolio/desktop-07-compare.png`
- `.tools/verification-local/review/ui-strategy-portfolio/mobile-04-compare.png`
- `.tools/verification-local/review/ui-strategy-portfolio/compare-ui-results.json`
- `.tools/verification-local/review/portfolio-parameter-variation.json`
- `scripts/acceptance-strategy-catalog.py`、`scripts/verification-strategy-portfolio.py`、`scripts/verification-portfolio-rejections.py`

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
- `vendor_adjusted` 复权价回放已实现（见下方验收）；因子数据未接通时仍会拒绝，不静默替换成交价。
- 真实价格验收（600519.SH 2025 日线，事件为受控 fixture）：100 股买入后，2025-07-15 以 100 元/股认购 30 股，成交与订单原因正确，期末现金 `847430.1425`、持仓 130 股，与买入成本、费用和认购款精确对账；两条因子版本冻结在 manifest；人为加入未解释的因子变动被拒绝。
- 部署验收：Run `8e6fe483-6ef6-4582-8b4e-cc0fbc50cd8f` 以 `rights_policy=skip` 完成，manifest 与 CSV/HTML 均披露策略与因子状态。

证据：

- `.tools/verification-local/review/rights-factor-report.json`
- `.tools/verification-local/review/rights-factor-deployed.json`
- `.tools/verification-local/review/design/backtest-rights-factor-1440.png`
- `.tools/verification-local/review/design/backtest-rights-factor-390.png`

### 公开页配股条款（真实双源，端到端）

- 来源：东方财富主源 + 新浪交叉校验（`publicweb.corporate.rights` / `publicweb.rights.v1`），默认关闭、显式开启 `COLLECTOR_PUBLIC_RIGHTS_FEED` 后才出现在来源目录；仅限内部研究，不得再分发。
- 校验规则：比例、配股价格、股权登记日、除权日、公告日任一不一致即失败；单边事件、占位日期（如 1900-01-01）、同一除权日多事件均拒绝发布。
- 真实事件：`300176.SZ` 鸿特科技 2026 年配股，每 10 股配 4 股、3.36 元/股，公告 2026-08-10、登记 2026-08-12、除权 2026-08-21、上市 2026-09-02，缴款窗口 2026-08-13 至 2026-08-19。
- 采集 Job `be55af30-9027-4562-8232-8e648c09c494`，单分区 `2026-08-10` 至 `2026-08-24`（北京时间）；Worker 导入数据集 `fd2f8bb3-d391-5b84-b7d9-953f5cc64ac6`（10 根日线，覆盖含停牌日）。
- 研究发布 `95d60447-4d8e-5bc9-b11e-17380d690ee4`，`content_version=454c2501…`；质量门禁为 `warning`，只披露 `knowledge_unknown`（公开页无法证明历史可知时间）。配股没有现金支付，不虚构 `payment_unknown` 或单一支付日。
- 策略 `d93f3ebf-991b-4cff-91db-2282db39628c` v1：先 `validated`，再用真实完成回测转为 `backtested`；qualification 从持久化 Run 推导，不再由调用方传 flag。
- `subscribe` Run `d6b60d0f-ee85-4225-b635-938651682f98`：2026-08-20T16:00Z（北京时间 08-21 除权日）认购 4000 股 @ 3.36，期末现金 `933360.00`、持仓 14000 股。
- `skip` Run `c3e1b6d1-d677-4927-9a66-0671f4219c71`：无 `rights_subscription` 订单，期末现金 `946800.00`、持仓 10000 股。
- 冻结执行时区：日线 bar 以交易所午夜打戳（`2026-08-21T00:00+08:00`），回测默认 `timezone_name=Asia/Shanghai`；显式传 `UTC` 仍可覆盖，但会把交易日错位一天，已加回归测试防止回退。
- 导出：CSV 保留 `rights_subscription` 机器原因与 `corporate_actions` 账本；可打印 HTML 显示「配股认购」与除权日；桌面和手机均通过「浏览公司行为版本」查看 4/10、3.36 元、登记/除权/上市日与质量警告，刷新后仍可见。

证据：

- `.tools/verification-local/review/rights-live-report.json`
- `.tools/verification-local/review/rights-events-desktop.png`
- `.tools/verification-local/review/rights-events-mobile.png`
- `tests/test_backtesting.py::test_daily_execution_defaults_to_exchange_zone_and_keeps_explicit_choice`
- `tests/test_mock_backtest_engine.py::test_exchange_midnight_bars_apply_rights_on_the_local_ex_date`
- `tests/test_strategy_qualification.py`

### 订单类型与 TIF（真实运行）

- 事件引擎实现 market / limit / stop / stop_limit 与 day / gtc / ioc / fok：
  - 限价买入在开盘价低于限价时按开盘价成交，否则在最低价触及限价时按限价成交；卖出对称。
  - 止损由最高/最低价触发，触发后按开盘价与触发价的较不利者成交；stop_limit 触发后再受限价约束。
  - IOC 在首个可成交 bar 成交可成交量并撤销剩余；FOK 不可全成则整单撤销；day 单在下一交易时段撤销；GTC 继续等待。
  - 成交量上限与现金约束仍先于 TIF 判定，撤销订单保留已成交数量为 0 或部分成交。
- 真实运行：Run `6232fa7e-e93a-4efc-ad0f-6e4599577c4b`（600519.SH 2025 日线）用止损单入场（1494.50）、限价单离场（1461.00），另有 day 限价单过期撤销和 FOK 限价单不可成交撤销；4 条订单状态为 买入成交 / 买入撤销 / 买入撤销 / 卖出成交，撤销单成交数量均为 0，期末空仓，现金 `996488.285` 与成交、费用精确对账。
- 报告展示「已取消」状态，CSV 导出包含撤销订单。

证据：

- `.tools/verification-local/review/order-types-report.json`
- `.tools/verification-local/review/order-types-result.json`
- `.tools/verification-local/review/design/backtest-order-types-1440.png`
- `.tools/verification-local/review/design/backtest-order-types-390.png`

### Walk-forward 参数寻优（真实运行）

- 参数网格随批次冻结（`parameter_grid_json`）；组合数、窗口数与运行总数有上限，超出直接拒绝。
- 每个窗口为「网格组合 × 训练/验证」各创建一个真实运行；读取时用训练区间报告逐候选打分并选出最优，验证区间报告仅对选中参数计入聚合。
- 选择规则：样本内累计收益最高；同收益时取最大回撤更小；再同则参数 JSON 字典序，保证确定性。
- 批次 `0f19bc2f-ed50-4cd2-b253-dd523bbf0828`（600519.SH 2025 日线，网格 `fast∈{2,5}`、`slow∈{10,20}`，训练 90 天/验证 45 天/步长 60 天）：
  - 5 个窗口，每窗口 4 个候选，全部完成
  - 选中参数随窗口变化（`fast=2,slow=20` → `fast=5,slow=20` → `fast=5,slow=10` → `fast=2,slow=10`）
  - 样本内收益 0.9476%、-0.3185%、-0.0311%、-0.2120%、-0.4020%，验证收益 0.7776%、-0.0071%、0.3293%、-0.2424%、0.0303%
  - 聚合：均值 0.18%、中位 0.03%、复利 0.89%、正收益窗口 60%、平均/最差回撤 0.22%/0.37%、成交 2
- 独立复核：从持久化批次读取每个窗口的全部候选训练报告，重新按规则选择，结果与批次记录逐窗口一致；选中验证运行 ID 与收益也与报告一致。
- 报告/导出展示网格、逐窗口选定参数、样本内收益与候选数。

证据：

- `.tools/verification-local/review/walk-forward-optimization-report.json`
- `.tools/verification-local/review/walk-forward-optimization-result.json`
- `.tools/verification-local/review/walk-forward-optimization-selection.json`
- `.tools/verification-local/review/design/walk-forward-optimization-1440.png`
- `.tools/verification-local/review/design/walk-forward-optimization-390.png`

### 停牌与涨跌停锁价（真实数据）

- 引擎把零成交量或 `quality=suspect` 的 bar 视为停牌：受影响订单被拒绝并记录原因 `halted`，GTC 订单保留等待复牌；停牌 bar 不进入策略历史，估值沿用停牌价。
- 日线一字板（open=high=low=close）按相对前一收盘价的涨跌幅识别方向：涨停锁死拒绝买入，跌停锁死拒绝卖出；无法判定时原因记为 `locked`。
- 报告、CSV、HTML 导出均展示状态与原因（停牌无法成交 / 涨停锁死 / 跌停锁死 / 价格锁死）。
- 真实运行：
  - `601995.SH` 数据集 `fd12682f-…`（2025-11-14 至 2025-11-20），最后一个真实 bar 为停牌 bar。买入成交后，卖出订单被拒绝，原因 `halted`，期末仍持有 100 股，现金 `999943.00` 与成交费用对账。
  - `600489.SH` 数据集 `a785982b-…`（2026-01-30 至 2026-02-05）含真实一字跌停 bar（33.21，-10.0%）。该日买入正常成交于 33.21，未被误判为涨停拒绝。
- 验收 run：`659bf772-7c5f-46b3-bf6b-fda8cafa0df9`（停牌）与 `5b417815-c867-436b-a159-913e641aeb9d`（跌停锁价）。

证据：

- `.tools/verification-local/review/market-constraints-report.json`
- `.tools/verification-local/review/market-constraints-halted.json`
- `.tools/verification-local/review/market-constraints-locked.json`
- `.tools/verification-local/review/design/backtest-halted-1440.png`
- `.tools/verification-local/review/design/backtest-halted-390.png`

### 市场冲击与涨跌停队列（真实数据）

- 执行模型新增 `impact_bps`：按成交占 bar 成交量的平方根计算冲击（`impact_bps × sqrt(participation)`），向不利方向调整成交价并重新按最小价位取整；`impact_bps=0` 时行为不变。
- 新增 `limit_queue_fraction`：当成交价位于当日涨跌停价时，可成交量上限为 `bar.volume × limit_queue_fraction`，模拟锁价处的排队比例。
- 两者随 manifest 冻结，报告「运行与输入」展示，工作台可配置。
- 真实运行：
  - 600519.SH 2025 日线，同一策略在 `impact_bps=0` 与 `200` 下买入价分别为 `1494.50` 与 `1494.67`（100 股多付 17 元），期末现金与成交价、费用、两次股息 `5163.00` 精确对账；run `aa7ac7e7-e9d7-40d5-b583-4ebc72738f7a`。
  - 600489.SH 的真实一字跌停 bar（33.21）：`limit_queue_fraction=1` 时买入成交于 33.21；`0.000001` 时队列容量不足一手，订单被拒绝（run `ec552671-…` 与 `bce16458-…`）。
- 说明：没有逐笔盘口数据，冲击与队列是 bar 级近似，不声称 L2 深度或成交概率。

证据：

- `.tools/verification-local/review/impact-queue-report.json`
- `.tools/verification-local/review/design/backtest-impact-1440.png`
- `.tools/verification-local/review/design/backtest-impact-390.png`
- 全量 `pytest` 通过记录：`.tools/verification-local/review/full-pytest.txt`（本轮修复 `test_bounded_supplier_http` 后无失败）

### 多 Worker 公平调度与租约心跳（真实两 Worker）

- 认领使用 `FOR UPDATE SKIP LOCKED`，按 `attempts, created_at` 排序；每个 Worker 同时只持有一个租约，多个 Worker 自然分摊队列。
- 运行期间每 `lease/3` 秒续租（`revision+1`，租约列更新）；发布结果前再续租一次，租约丢失时跳过发布，避免重复结果。
- 租约过期的运行由扫描任务改回 `queued`（保留 attempts），只有超过最大尝试次数才标记 `lease_expired` 失败；认领只接受 `queued`，因此生命周期显式为 `queued → running → queued → running → completed`。
- 迁移 `0041` 放宽触发器：仅允许 `running → queued` 且必须清空租约，终态仍不可变。
- 真实验收（两个 Worker，租约 1 秒，真实 600519 队列）：
  - 3 个并行运行全部完成，attempts 均为 1，结果对象齐全，两个 Worker 都实际处理了运行
  - 运行期间观测到租约到期时间被心跳推进（同一运行的 `lease_expires_at` 连续增大）
  - SIGKILL 持有运行的 Worker 后，运行经 `queued` 重新入队，由另一个 Worker 完成；attempts=2，history 为 `queued → running → queued → running → completed`，结果唯一
- 证据：`.tools/verification-local/review/lease-fairness.json`

### 协作式取消（真实两 Worker）

- Worker 在运行期间每 0.25 秒读取持久化运行状态；观察到 `cancelled` 后设置中断标记。
- 引擎按 `QTRADE_BACKTEST_SIGNAL_CHUNK`（默认 1000 根）分块调用 Runner，块间与每个 bar 前检查中断；命中后抛出 `ExecutionCancelled`，Worker 直接返回，不写失败事件也不发布结果。
- 取消仍可由 API 完成；快照/结果与历史保持不可变。
- 真实验收（两个 Worker、信号块 5 根、租约 10 秒）：
  - 取消一个运行中的 600519 回测，0.045 秒内持久化状态变为 `cancelled`
  - Worker 在块间协作中止（日志 `backtest cancelled cooperatively`），history 为 `queued → running → cancelled`，attempts=1，无结果对象
  - 同一时刻另一个 Worker 的运行正常完成且仅执行一次
- 证据：`.tools/verification-local/review/cooperative-cancel.json`

### 逐笔（tick）数据集回测（真实数据）

- 市场 schema 允许 `granularity=tick`；已发布的 tick 数据集可通过 `POST /data/tasks/{id}/dataset?output=tick` 从真实逐笔原始批次发布（转换版本 `mairui.trade-tick.v1`，一笔一 Bar）。
- 回测接受 tick 数据集：按 `tick_aggregation_seconds`（默认 3 秒，工作台可配）聚合为执行 bar，策略与撮合都基于聚合 bar；聚合窗口冻结进 manifest，报告展示。
- walk-forward 规划仍拒绝 tick 数据集（窗口样本计数以 bar 为单位），已明确保留。
- 真实验收（600519.SH，2026-08-14 真实逐笔，5 个原始分页）：
  - tick 数据集 `657f756e-9946-5f38-a680-21f3b50b080c`
  - Run `701bab74-2e27-4358-ba14-ad5e47395fbb`，60 秒聚合得到 257 根执行 bar
  - 买入 100 股 @ 1355.00，费用 40.65，期末现金 `864459.35` 与成交精确对账
  - 工作台可选中 tick 数据集并配置聚合窗口，报告显示「tick 聚合窗口 60 秒」
- 证据：`.tools/verification-local/review/tick-backtest-report.json`、`tick-backtest-result.json`

### 供应商复权价回放（真实价格 + fixture 因子）

- `vendor_adjusted` 模式把冻结因子序列按 `factor(t)/factor(start)` 缩放到执行 bar 的 OHLC；现金分红/送转/拆并/配股在该模式下**不入账**，避免与复权价重复计入。
- 因子必须覆盖数据集全部标的，缺失时拒绝；因子变化仍需有对应公司行为。
- 工作台可选择价格口径，manifest 冻结 `adjustment_factor_mode` 与状态，报告与 CSV/HTML 展示。
- 真实验收（600519.SH 2025 日线 + 验收 fixture 因子，真实除息日对齐）：
  - Vendored Run `cbcf81fc-3a17-4d11-a904-fafd2cdb86a0`，manifest 状态 `applied_to_execution_prices`
  - 2025-06-29 收盘价由 `1409.52` 缩放到 `1395.4248`（0.99 倍）
  - 复权模式期末现金 `850505.165`，账本模式 `855668.165`，差额恰为两次股息 `5163.00`
  - 两种模式现金均与成交、费用精确对账
- 限制：因子值为验收 fixture（供应商因子 feed 未接通）；公司行为同样为真实除息日上的 fixture 条款。

证据：

- `.tools/verification-local/review/vendor-adjusted-report.json`

### 逐笔价位量深度模型（真实逐笔数据）

- tick 数据集会额外构建「执行窗口 × 价位 → 成交量」的深度图，冻结为 `depth_model=tick_price_levels`；非 tick 数据集为 `bar_approximation`。
- 撮合规则：
  - 成交价取**符合委托条件的价位 VWAP**（市价单为窗口全部价位；限价单只统计不劣于限价的价位），并按最小价位取整。
  - 可成交容量上限为「符合条件价位的总成交量」与 `max_volume_fraction × bar.volume` 的较小者；限价单或锁价成交再乘以 `limit_queue_fraction × 触及价位成交量`，模拟排队优先度。
  - 现金不足时逐手减少数量并重算价位 VWAP，保持现金非负。
- 报告、CSV、HTML 导出展示撮合深度模型；工作台说明同一条执行路径。
- 真实验收（600519.SH 2026-08-14 真实逐笔，60 秒窗口，run `b22ea6fa-c52f-4e56-8256-a6168fc1fca8`）：
  - manifest 冻结 `depth_model=tick_price_levels`、`tick_aggregation_seconds=60`
  - 成交窗口内 13 个价位；独立重算该窗口 VWAP 得 `1352.71`，与报告成交价完全一致
  - 买入 100 股、费用 `40.5813`，期末现金 `864688.4187` 与成交精确对账
  - 报告显示「逐笔价位量（按价位走量计算成交价与容量）」，CSV 含 `depth_model,tick_price_levels`
- 限制：逐笔成交价与成交量是深度代理，不是 L2 快照；真实盘口队列与逐笔成交概率仍不建模。

证据：

- `.tools/verification-local/review/tick-depth-report.json`
- `.tools/verification-local/review/tick-depth-result.json`
- `.tools/verification-local/review/tick-depth-vwap.json`
- `.tools/verification-local/review/design/backtest-tick-depth-1440.png`
- `.tools/verification-local/review/design/backtest-tick-depth-390.png`

### 真实供应商复权因子（端到端）

- 新增研究能力 `mairui.adjustment.factors`（kind=`adjustment_factor`）：对每个标的请求供应商前复权（`f`）与不复权（`n`）日线，按 `前复权收盘 / 不复权收盘` 推导因子序列；复权收盘按分取整造成的日间噪声用 0.5% 阈值过滤，只在真实除权日产生阶跃。
- 采集端注册该能力并移除 `extension.adjustment_factor` 的「未接通」占位；复权价格请求被归类为 `research.data` 证据（而不是普通 bar 采集）后才进入研究归档。
- 端到端验收（真实供应商数据，600519.SH，2025 全年）：
  - 研究任务 `1cd317e0-8547-4cb8-894d-01b370d7662c` 经计划预览（1 分区、2 次调用）提交，采集 Worker 完成并发布 `38b89f8c-c466-5ada-85c2-79ce38be8d35`
  - 发布记录 3 条：2025-01-02 `0.94647177`、2025-06-26 `0.96339437`、2025-12-19 `0.98012766`，与真实除息日一致
  - 回测 Run `824f6854-5679-43a9-bc59-11302a209957` 以 `vendor_adjusted` 运行并完成，manifest 冻结 3 条因子及其内容版本
  - 独立复核：2025-06-29 原始收盘 `1409.52` × 因子比例 `1.0178796669` = `1434.72174812`，与权益曲线持仓隐含价完全一致；期末现金 `850505.165`（复权模式不计股息）与买入成本、费用精确对账
- 限制：因子是价格比值的推导值，不是供应商原生因子字段；配股 feed 已另行通过公开页双源接通（仅内部研究，见上）。

证据：

- `.tools/verification-local/review/vendor-feed-report.json`
- `.tools/verification-local/review/vendor-feed-result.json`
- `.tools/verification-local/review/vendor-feed-vwap.json`

## 5. 用户体验改进

- 回测表单按策略版本 schema 渲染参数字段，不再要求用户手写 JSON。
- 策略版本选择显示策略名和版本号，运行历史显示对应策略和数据集，而不是 UUID 片段。
- 运行中每 4 秒刷新；提供“刷新状态”按钮，避免排队状态长期不更新。
- 完成运行可勾选 2–20 个进行对比；保留“对比最近两次完成回测”快捷入口。
- 日线回测显示“收盘后可用、无盘中时段过滤”，不再显示误导性的盘中午休假设。
- 报告指标统一保留两位；显示参数、代码/数据集摘要、引擎和 seed；超过 100 条订单/成交显示截断说明。
- 表单明确展示当前模型边界：逐笔数据按窗口聚合并使用价位量深度、无 L2 快照，官方历史成员区间未接入；现金分红、送转/拆并、配股策略、供应商因子冻结核对、T+1、整手、最小价位和成员区间校验已建模。

## 6. 仍不满足的专业能力

以下能力未完成，不能宣称“专业研究/实盘就绪”：

- 复权因子与配股条款均已接通真实来源。复权因子由供应商前复权/不复权收盘价推导（供应商只发布复权后的价格序列，没有独立因子字段），并按 0.5% 阈值只在事件日形成阶跃。
- 配股条款来源为东方财富公开接口 + 新浪公开页交叉校验，默认关闭、显式开启后可用于内部研究；比例、价格、登记日、除权日或公告日任一不一致即拒绝发布。公开源不可再分发，缴款日期只有窗口、历史可知时间未知，均已在 UI 与质量门禁中披露；Wind 等商业授权仍保留为后续升级路径，见 `docs/data/RIGHTS-FEED-CONDITIONS.md` 与 `docs/data/SUPPLIER-RIGHTS-COVERAGE.md`。
- 供应商历史成员区间不可用；当前只能使用工作区定义或供应方区间，非官方区间仍可能低估幸存者偏差。
- 逐笔数据（`granularity=tick`）可作为已发布数据集参与回测：按 `tick_aggregation_seconds` 聚合为执行 bar（OHLCV），策略在聚合 bar 上运行；撮合使用按执行窗口聚合的**逐笔价位量**（`depth_model=tick_price_levels`），成交价取符合委托条件的价位 VWAP，容量与锁价队列按价位成交量限制。无 L2 快照，价位量是逐笔成交的可观测深度代理。
- 停牌与锁价已建模：停牌 bar（零成交量或数据源标记 suspect）会拒绝受影响订单并标记 `halted`；一字涨/跌停按前一收盘价的涨跌幅识别，买入在涨停锁死、卖出在跌停锁死时被拒绝，原因分别为 `limit_up` / `limit_down`；无法判定方向时标记 `locked`。逐笔数据已支持价位量深度，L2 快照仍未接入。
- 未实现基准相对指标（alpha/beta/超额/跟踪误差/信息比率）与基准时间对齐。
- Walk-forward 支持冻结参数网格：每个窗口先在训练区间运行全部候选，按样本内收益选择（同收益取回撤更小、参数 JSON 更小者），再用选中参数执行验证区间；聚合只统计选中参数的样本外结果。
- 回测任务使用持久队列 + 多 Worker 消费：`SKIP LOCKED` 原子认领、按尝试次数/创建时间排序、运行期间租约心跳续期；租约过期的运行先回到队列再被重新认领，超过重试次数才失败。
- 已提供 CSV 与可打印 HTML（另存为 PDF）导出；未提供服务端直接生成 PDF 的接口。
- 风险引擎尚未接入回测/Paper 订单路径；策略版本 qualification 的 `backtest_succeeded` 已改为从持久化完成 Run 与精确 `strategy_version_id` 推导，不再接受调用方 flag。Paper observe 计数仍未接入，进入批准/实盘前仍需补齐。

## 7. 下一步优先级

1. 官方历史成员区间、Wind 等商业配股授权与退市事件。
2. L2 盘口深度、真实队列与逐笔成交概率。
3. 多 Worker 公平调度、租约心跳和执行中断。
4. 供应商历史成员区间接入与官方退市事件。
5. 基准相对指标与 Walk-forward 更丰富的选择目标（夏普/Calmar）。
6. 风险引擎与 Paper/实盘订单路径统一。
