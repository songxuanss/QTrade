# 量化模型执行

## Purpose

让已保存的量化策略在真实行情上执行，并让结果可复现、可解释。

## ADDED Requirements

### Requirement: 隔离执行存储的模型
系统 MUST 在独立 Runner 中执行 Strategy Version 源码，按有序 bar 逐个调用策略；API 和 Web 进程 MUST NOT 直接执行用户 Python 源码。

#### Scenario: 真实策略执行
- **WHEN** 用户对已校验版本提交回测
- **THEN** 系统读取冻结源码并通过 Runner 生成逐 bar 订单提案，未配置 Runner 时明确失败

### Requirement: 因果信号与版本冻结
每个订单提案 MUST 只读取当前及历史 bar；回测 MUST 冻结代码摘要、数据集摘要、参数、执行模型和随机种子。

#### Scenario: 重复运行
- **WHEN** 同一 manifest 重复执行
- **THEN** 订单、成交与指标一致，报告中的 seed 与 manifest 一致

### Requirement: 现实成交与绩效
回测 MUST 在现金和成交量约束后按 lot 取整，日线与分钟 session 区分，绩效从初始资金计算。

#### Scenario: 首个交易周期亏损
- **WHEN** 第一个 bar 后权益低于初始资金
- **THEN** 最大回撤与年化收益反映该亏损

### Requirement: 结果完整性
报告 MUST 校验 run、manifest、币种、seed、样本数和期末权益；不一致时拒绝展示。

#### Scenario: 结果被替换
- **WHEN** 结果文件与运行/manifest 不匹配
- **THEN** 报告返回校验错误，不显示错误指标


### Requirement: 持久队列与重启恢复
回测创建 MUST 只持久化 queued run 并由 Worker 认领执行；Worker MUST 使用租约、尝试次数和取消标记，重启后恢复过期租约，取消后不得发布迟到结果。

#### Scenario: Worker 停止后重启
- **WHEN** run 已 queued 且 Worker 在认领前停止
- **THEN** 完整重启后 Worker 认领该 run，历史记录 queued → running → completed

#### Scenario: 取消已排队运行
- **WHEN** 授权用户在 queued 或 running 时取消
- **THEN** run 标记 cancelled，Worker 不写入结果对象，重复取消被拒绝


### Requirement: 公司行为账本
回测 MUST 在除权日按冻结的已发布公司行为处理现金分红、送转和拆并；现金按除权前持仓计算，送转不增加当日可卖数量，拆并缩放持仓和可卖数量；配股 MUST 按冻结策略认购或跳过；未知口径 MUST 拒绝而不是猜测，事件内容和版本 MUST 冻结进 manifest。

#### Scenario: 除息持有
- **WHEN** 持有证券跨过已发布现金分红除权日
- **THEN** 现金按每 10 股派息和除权前持仓入账，持仓数量不变

#### Scenario: 送转与拆并
- **WHEN** 发生送股、转增或拆并
- **THEN** 持仓和可卖数量按规则调整；送转新增股份不在当日可卖

#### Scenario: 事件版本冲突或缺失
- **WHEN** 同一事件存在冲突版本或缺少除权日
- **THEN** 回测拒绝或明确报告不支持，不静默忽略或猜测

### Requirement: Walk-forward 逐窗执行
Walk-forward MUST 从已发布数据集的真实 bar 时间戳生成互不重叠的验证窗口，并为每个验证窗口创建一个持久回测运行；读取结果时 MUST 校验每个完成窗口的不可变报告，并按完成窗口聚合收益、回撤和成交数，失败或取消窗口 MUST 单独计数且不进入聚合。

#### Scenario: 滚动验证执行
- **WHEN** 授权用户提交有效的策略版本、数据集和窗口参数
- **THEN** 每个验证窗口生成一个 queued 回测运行，最终通过 Worker 完成并写入独立报告

#### Scenario: 部分窗口失败
- **WHEN** 至少一个窗口失败而其他窗口完成
- **THEN** 批次状态为 partial，失败窗口保留错误码且不参与均值/中位数计算

#### Scenario: 结果复核
- **WHEN** 用户查看 Walk-forward 汇总
- **THEN** 聚合值等于各完成窗口报告的可复核统计，且界面说明训练区间未用于参数优化

### Requirement: 报告导出
已完成的回测运行和 Walk-forward 批次 MUST 能导出 CSV 与可打印 HTML；导出 MUST 只使用已校验的不可变结果和冻结 manifest，MUST 包含指标、冻结输入、公司行为及其事件版本、订单与成交，并 MUST 要求读取权限、以附件形式下载且禁止缓存。

#### Scenario: 下载已完成回测
- **WHEN** 授权用户请求已完成运行的 CSV 或 HTML 导出
- **THEN** 返回附件，内容与该运行的持久化报告和 manifest 数值一致

#### Scenario: 未完成或未授权
- **WHEN** 运行尚未完成，或请求没有有效会话
- **THEN** 分别返回冲突和未授权，不生成不完整或匿名可读的导出

#### Scenario: Walk-forward 导出
- **WHEN** 用户导出 Walk-forward 批次
- **THEN** 文件包含聚合指标、逐窗口状态与错误码，并说明训练区间未用于参数寻优

### Requirement: 成员区间与幸存者偏差披露
回测 MUST 支持显式的成员区间模式：数据集内每个标的必须有已登记的有效区间，否则拒绝创建；区间与覆盖统计 MUST 冻结进 manifest，报告与导出 MUST 披露幸存者偏差程度。交易 MUST 只发生在区间内，区间结束时 MUST 按最后可用收盘价强制平仓并标记原因。

#### Scenario: 区间结束前强制平仓
- **WHEN** 持有一个在运行结束前退出成员区间的标的
- **THEN** 最后一根区间内 bar 按收盘价卖出全部持仓，订单原因为成员区间结束，现金与费用入账

#### Scenario: 缺少区间
- **WHEN** 成员区间模式下数据集存在没有区间的标的
- **THEN** 回测拒绝创建并列出缺失标的，不静默退化为数据集模式

#### Scenario: 报告披露
- **WHEN** 用户查看报告或导出
- **THEN** 展示区间、内容版本、全程/不完整/未知覆盖计数和幸存者偏差说明

### Requirement: 配股与供应商复权因子
回测 MUST 按冻结的配股策略处理配股：`subscribe` 用可用现金认购并在结果中产生可对账的认购成交，`skip` 保留现金；复权因子 MUST 随 manifest 冻结版本，并 MUST 校验因子变化都有对应公司行为，未解释变动、混合复权口径或越界标的 MUST 拒绝运行。复权价回放 MUST 在未实现时显式拒绝并披露，不得静默替换成交价。

#### Scenario: 配股认购
- **WHEN** 持有标的跨越配股除权日且策略为认购
- **THEN** 按基准股数与可用现金认购，生成原因=配股认购的成交，现金与持仓与账本一致

#### Scenario: 因子缺解释
- **WHEN** 供应商因子在某日变化但当天没有冻结的公司行为
- **THEN** 回测拒绝创建并指出日期，不静默使用或忽略因子

#### Scenario: 复权价模式
- **WHEN** 用户请求 `vendor_adjusted` 复权价回放
- **THEN** 请求被拒绝并说明仅支持冻结核对的账本模式

### Requirement: 订单类型与 TIF
事件引擎 MUST 支持 market、limit、stop、stop_limit 订单与 day、gtc、ioc、fok 有效期；限价 MUST 不劣于限价成交，止损 MUST 由最高/最低价触发；IOC MUST 成交可成交量后撤销剩余，FOK MUST 在无法全成时整单撤销，day 单 MUST 在下一交易时段撤销，GTC MUST 持续有效；撤销 MUST 以订单状态体现且不产生虚假成交。

#### Scenario: 限价与止损
- **WHEN** 限价买入低于开盘价但最低价触及限价
- **THEN** 按限价成交；止损买入在最高价触及止损价时触发并按较不利价格成交

#### Scenario: IOC/FOK/day
- **WHEN** IOC 只能部分成交、FOK 无法全成、day 单当日未成交
- **THEN** 分别产生部分成交后撤销、整单撤销、下一时段前撤销，并保留可对账的成交状态

#### Scenario: 真实对账
- **WHEN** 混合订单类型运行完成
- **THEN** 期末现金与持仓可由成交、费用和撤销状态复核

### Requirement: Walk-forward 参数寻优
Walk-forward MUST 支持冻结参数网格；每个窗口 MUST 在训练区间执行全部候选并按样本内收益选择参数（同收益取回撤更小，再取参数 JSON 字典序），再以选中参数执行验证区间；聚合 MUST 只使用选中参数的验证结果，批次与导出 MUST 记录网格、选定参数、样本内收益和候选数。

#### Scenario: 网格选择
- **WHEN** 提交参数网格与窗口参数
- **THEN** 每个窗口生成候选的训练与验证运行，选择结果可追溯到具体候选运行

#### Scenario: 选择确定性
- **WHEN** 两个候选样本内收益相同
- **THEN** 依次按较小最大回撤和参数 JSON 字典序选择，重复读取结果一致

#### Scenario: 导出与报告
- **WHEN** 用户查看或导出 Walk-forward 结果
- **THEN** 展示参数网格、逐窗口选定参数、样本内收益与候选数

### Requirement: 停牌与涨跌停锁价
事件引擎 MUST 从 bar 数据识别停牌与一字涨跌停：零成交量或数据源标记为不可用/存疑的 bar MUST 不允许成交，受影响订单 MUST 以明确原因拒绝或保留（GTC），停牌 bar MUST NOT 进入策略历史；一字板 MUST 按相对前一收盘价的涨跌幅判定方向，涨停锁死拒绝买入、跌停锁死拒绝卖出，无法判定时记录 locked。结果、报告与导出 MUST 展示相应状态与原因。

#### Scenario: 停牌无法卖出
- **WHEN** 持有标的在真实停牌 bar 上收到卖出订单
- **THEN** 订单被拒绝并标记 halted，持仓保留，现金不变

#### Scenario: 跌停锁价
- **WHEN** 一字跌停 bar 上收到买入订单
- **THEN** 买入允许成交于锁价；同一 bar 的卖出被拒绝并标记 limit_down

#### Scenario: 导出可解释
- **WHEN** 用户查看报告或导出
- **THEN** 订单状态与原因（停牌无法成交/涨停锁死/跌停锁死/价格锁死）可见

### Requirement: 市场冲击与涨跌停队列
回测 MUST 支持冻结的市场冲击与队列参数：冲击 MUST 按成交占 bar 成交量的平方根计算并计入成交价，锁价 bar 的可成交量 MUST 受 `limit_queue_fraction × volume` 限制；参数 MUST 冻结进 manifest 并在报告中可见。无 L2 数据时 MUST 明确说明这是 bar 级近似。

#### Scenario: 冲击改变成交价
- **WHEN** 同一策略在同一真实行情上启用更高冲击系数
- **THEN** 买入成交价上升，期末现金与成交价、费用和公司行为精确对账

#### Scenario: 锁价队列容量
- **WHEN** 涨跌停锁价 bar 的队列比例使可成交量低于一手
- **THEN** 订单不成交并以明确状态记录

### Requirement: 多 Worker 公平调度与租约心跳
回测队列 MUST 支持多个 Worker 安全并发认领：使用 `SKIP LOCKED` 原子认领，运行期间 MUST 续租，租约过期的运行 MUST 先回到队列并保留尝试次数，只有超过重试预算才失败；租约丢失的 Worker MUST NOT 发布结果。

#### Scenario: 两个 Worker 并行
- **WHEN** 多个 Worker 同时消费队列
- **THEN** 每个运行只被一个 Worker 执行一次，结果唯一，租约到期时间随心跳推进

#### Scenario: Worker 被杀
- **WHEN** 持有运行的 Worker 被强制终止且租约过期
- **THEN** 运行回到 `queued`，被其他 Worker 重新认领并完成，历史保留两次 running

### Requirement: 运行中协作式取消
Worker MUST 在运行期间观察持久化取消状态，并在策略分块之间检查中断；取消后 MUST 停止执行、保持运行状态为 cancelled、记录取消历史且不发布结果对象。单次 Runner 调用不可中断时 MUST 在返回后的下一个检查点停止。

#### Scenario: 取消运行中的回测
- **WHEN** 授权用户取消一个处于 running 的运行
- **THEN** Worker 在下一个块边界停止，history 为 queued → running → cancelled，attempts 不变且无结果对象

#### Scenario: 取消不影响其他 Worker
- **WHEN** 一个 Worker 的运行被取消而另一个 Worker 正在执行
- **THEN** 另一个运行正常完成且只执行一次

### Requirement: 逐笔数据集回测
已发布的 tick 数据集 MUST 可用于回测：引擎 MUST 按冻结的聚合窗口把逐笔打印聚合为执行 bar（OHLCV），并在聚合 bar 上运行策略与撮合；聚合窗口 MUST 写入 manifest 并在报告中可见。数据发布 MUST 支持从真实逐笔原始批次生成 tick 数据集。

#### Scenario: 真实逐笔回测
- **WHEN** 用户用已发布的 tick 数据集提交回测并设置聚合窗口
- **THEN** 引擎按窗口聚合逐笔、完成撮合，成交与现金可与报告对账

#### Scenario: 聚合可见
- **WHEN** 查看 tick 回测报告
- **THEN** 展示使用的 tick 聚合窗口

### Requirement: 供应商复权价回放
回测 MUST 支持 `vendor_adjusted`：按冻结因子序列相对运行起点的比例缩放执行 bar 的 OHLC，并 MUST 在该模式下禁用现金分红、送转、拆并与配股入账以避免重复计入。因子缺失或口径冲突 MUST 拒绝运行；模式与披露 MUST 写入 manifest 并在报告与导出中展示。

#### Scenario: 复权价格
- **WHEN** 除权日后启用 vendor_adjusted
- **THEN** 执行价格按因子比例缩放，资金按复权价结算

#### Scenario: 分配不重复
- **WHEN** 同一运行同时存在公司行为与复权因子
- **THEN** 复权模式不记现金股息，账本模式记股息，两者现金差额等于股息

### Requirement: 逐笔价位量深度
tick 数据集 MUST 在聚合执行窗口的同时构建「价位 → 成交量」深度，并在 manifest 冻结 `depth_model=tick_price_levels`；撮合成交价 MUST 取符合委托条件的价位成交量 VWAP，容量 MUST 受符合条件价位总量与 `max_volume_fraction` 约束，限价/锁价成交 MUST 再受 `limit_queue_fraction × 触及价位成交量` 约束。非 tick 数据集 MUST 使用显式的 bar 级近似并同样披露。报告与导出 MUST 展示当前深度模型。

#### Scenario: 价位 VWAP 成交
- **WHEN** tick 数据集回测产生成交
- **THEN** 成交价等于该执行窗口符合条件价位的成交量 VWAP，且可由原始逐笔独立重算

#### Scenario: 队列容量
- **WHEN** 限价成交且队列比例使触及价位可成交量低于委托量
- **THEN** 只成交队列允许的数量，剩余按 TIF 处理

#### Scenario: 模式披露
- **WHEN** 查看 tick 回测报告或导出
- **THEN** 显示 `tick_price_levels` 深度模型；非 tick 运行显示 bar 级近似


### Requirement: 真实供应商复权因子采集
系统 MUST 能从真实供应商采集复权因子证据：以供应商前复权与不复权收盘价推导因子序列，MUST 过滤分位取整造成的日间噪声，只在真实调整事件日形成阶跃；采集 MUST 经计划预览、预算确认、研究发布后，由回测冻结其内容版本并应用于 `vendor_adjusted` 执行价格。因子来源不可用时 MUST 拒绝 `vendor_adjusted`，不得回退到占位数据。

#### Scenario: 供应商因子采集
- **WHEN** 授权用户提交 adjustment_factor 研究任务
- **THEN** 采集端请求复权与不复权价格并发布因子记录，记录日期与真实除权日一致

#### Scenario: 回测应用
- **WHEN** 回测以 vendor_adjusted 使用该发布
- **THEN** manifest 冻结因子内容版本，执行价格按 factor(t)/factor(start) 缩放且可由原始收盘价独立复算

#### Scenario: 来源不可用
- **WHEN** 供应商因子缺失或采集失败
- **THEN** vendor_adjusted 请求被拒绝，不回退到 fixture 或静默使用未复权价
