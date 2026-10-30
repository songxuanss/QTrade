# QTrade

QTrade 是一个面向专业交易者和量化研究员的交易分析、告警、策略、回测与受控执行平台。该词汇表定义产品与领域文档共享的语言，不描述实现细节。

## Language

### 访问与协作

**Workspace**:
一组成员、市场、策略、数据集、账户与告警规则共享的协作边界。
_Avoid_: Tenant, Organization, Team

**Membership**:
一个用户在某个 Workspace 中的成员关系及其角色。
_Avoid_: Permission, Role

**QTrade User**:
由 SSO 身份映射而来的 QTrade 本地用户，不保存登录密码。
_Avoid_: Account, Owner

### 市场数据

**Collection Publication**:
一次采集所产生、带有来源与覆盖证据的不可变数据产物，供市场展示或研究消费。
_Avoid_: Download success, Market Dataset

**Factor Point**:
某个标的在一个交易日的指定因子观测值，带有定义版本及可知时间证据；缺失是独立状态。
_Avoid_: Bar, Financial report

**Instrument**:
一个可在特定市场交易的标的及其精度、合约规格、交易时段等静态定义。
_Avoid_: Symbol, Ticker

**Market Dataset**:
一次可复现研究或回测引用的标准化市场数据集合。
_Avoid_: Data source, Data table

**Data Batch**:
一次采集或补采产生的不可变原始数据批次。
_Avoid_: Job, Import

**Raw Record**:
供应商记录的原始字节、内容类型与批次内采集位置。保留精度文本、格式及采集顺序，
由声明版本的转换规则解释为标准数据。
_Avoid_: Bar, 标准化行情

**Data Quality Issue**:
数据缺失、重复、乱序、异常跳价或其他阻碍可信使用的检测结果。
_Avoid_: Error, Warning

### 研究与策略

**Strategy**:
用户创建并拥有的策略概念及其生命周期，不指向某个具体代码版本。
_Avoid_: Algorithm, Bot

**Strategy Version**:
Strategy 在某份代码、参数 schema 与依赖环境下的不可变版本；后续变更形成新的 Strategy Version。
_Avoid_: Strategy, Code

**Strategy Version State**:
某个不可变 Strategy Version 在校验、回测、模拟观察、审批或执行流程中的当前阶段；每次变化都保留对应的转换事实。
_Avoid_: Strategy Status, Run Status

**Strategy Version Approval**:
risk_manager 对某个确切 Strategy Version 及其回测和模拟观察证据作出的不可变发布决定。
_Avoid_: Strategy Approval, Live Permission

**Strategy Static Validation**:
针对一份策略源码和依赖清单执行的静态预检；只输出语法、依赖、已识别能力和资源风险诊断，不执行策略代码，也不能替代隔离运行时。
_Avoid_: Sandbox, Execution

**Backtest Run**:
对固定 Strategy Version、Market Dataset、参数和成本模型执行的一次历史模拟。
_Avoid_: Experiment, Simulation

**Backtest Comparison**:
对多次已完成 Backtest Run 的输入、结果曲线与绩效指标进行并排分析，并标明影响直接可比性的差异。
_Avoid_: Experiment, Ranking

**Walk-forward Window**:
一组有序的训练时间区间与其后的验证时间区间；验证样本不得进入对应训练输入。
_Avoid_: Backtest Run, Date Range

**Backtest Run Event**:
记录 Backtest Run 某个递增版本状态与进度的追加式事实；事件用于恢复和审计，不替代当前运行状态。
_Avoid_: Progress Log, Mutable History

**Artifact Manifest**:
描述一次研究、回测或执行产物输入来源与版本的不变元数据。
_Avoid_: Metadata, Log

**Score Artifact**:
模型或因子在特定时点和标的集合上输出的标准化分数。
_Avoid_: Signal, Prediction

### 交易与风控

**Strategy Signal**:
策略针对行情产生的买卖提议，尚未绑定具体执行账户与运行时；不能作为可提交订单。
_Avoid_: Order Intent, Order Command, Score Artifact

**Order Intent**:
策略或用户产生的交易意愿，尚未通过风控，也不是可提交订单。
_Avoid_: Order, Signal

**Order**:
由已授权 Order Command 创建、具有独立状态与成交累计量的执行记录。
_Avoid_: Order Intent, Broker Order

**Order Event**:
记录一个 Order 状态版本、发生时间、来源与成交累计量的追加事实。
_Avoid_: Order, Log

**Risk Decision**:
RiskEngine 对 Order Intent 的允许、拒绝或要求审批结论及其原因。
_Avoid_: Permission, Check

**Order Command**:
通过 Risk Decision 后可提交给 Broker Adapter 的幂等下单命令。
_Avoid_: Order Intent, Request

**Paper Runtime**:
使用模拟撮合但尽量复用真实执行接口的策略运行时。
_Avoid_: Demo, Simulator

**Live Runtime**:
经审批后连接真实 Broker Adapter 的策略运行时。
_Avoid_: Production strategy, Bot

**Broker Account**:
外部券商或交易所中归属明确的交易账户。
_Avoid_: QTrade User, Wallet

**Reconciliation**:
对 QTrade 本地订单、成交、持仓与 Broker Account 侧状态的一致性核对。
_Avoid_: Sync, Repair

**Kill Switch**:
暂停新订单并可撤销未成交订单的全局安全控制。
_Avoid_: Disable button, Stop loss

### 告警与审计

**Alert Rule Version**:
告警条件、作用域、冷却及通知配置在特定时点的不可变定义。
_Avoid_: Alert, Signal

**Alert**:
某条告警规则被满足时产生、可认领与处置的异常记录。
_Avoid_: Notification, Data Quality Issue

**Notification Attempt**:
针对一个告警向一个目标发送通知的一次尝试及其结果。
_Avoid_: Alert, Delivery

**Audit Event**:
某个主体对指定业务对象采取动作时留下的不可修改事实记录。
_Avoid_: Log, Alert

### 成交与风险事实

**Fill**:
某个订单已执行的一部分或全部数量及其成交价格、成本与时间事实。
_Avoid_: Order, Order Intent

**Risk Event**:
风控检查、限额或安全控制所产生、可追溯原因与影响范围的业务记录。
_Avoid_: Alert, Risk Decision

### 历史下载覆盖

**Collection Coverage**:
同一工作区、来源授权、标的和粒度中已完成采集的时间范围。
_Avoid_: Task Count, Tick Count

**Missing Range**:
用户请求中尚未被完整采集覆盖的时间范围。
_Avoid_: Failed Task, Empty Batch

**Checkpoint Resume**:
保留已经保存的采集结果，从已确认的进度继续处理未完成部分。
_Avoid_: Full Redownload, Completed Coverage

**Research Publication**:
从已验证的原始批次生成可查询的不可变研究数据集，独立于采集完成与引擎执行。
_Avoid_: Download Completion, Backtest Completion

**Observed Minute**:
至少包含一个来源逐笔记录的一分钟窗口；没有记录的分钟不自动补齐。
_Avoid_: Trading Session Coverage, Exchange Sequence

**Source Capability**:
当前配置允许提交的来源、授权、粒度与已知时间边界；不代表来源健康或整个历史区间完整。
_Avoid_: Source Completeness, Trading Readiness

**Trading Day Coverage**:
某个标的请求范围中，来源日历预期交易日与实际观察到行情的日期对比；不证明每个盘中区间完整。
_Avoid_: Collection Coverage, Intraday Completeness

**Research Readiness**:
冻结数据集已核验的研究前提与尚缺条件；基础行情可查询不等于回测或交易已经就绪。
_Avoid_: Download Completion, Live Readiness

### 股票池与批量采集

**Market Universe**:
按明确成员规则组织的市场观察或采集标的集合，可来自自定义选择、指数成分、行业、主题或市值排名。
_Avoid_: Watchlist, Sector, Market Dataset

**Universe Snapshot**:
某个 Market Universe 在具体观察时间形成的不可变成员版本，保留成员选择的来源和口径；当前快照不代表历史成员有效性。
_Avoid_: Historical Constituents, Live Members

**Collection Campaign**:
对冻结的标的成员、范围、周期与采集规则执行的一次聚合采集运行，包含逐标的结果及整体进度。
_Avoid_: Data Batch, Market Dataset, Single Task

**Market Theme**:
按有明确依据的主题规则组织的观察分类，一个标的可属于多个主题；主题归属不等于已证实的企业供货关系。
_Avoid_: Industry, Official Index, Supplier Relationship

### 数据来源与历史语义

**Data Category**:
由相同市场事实与字段语义构成的数据类别，例如价格Bar、行情快照、成交逐笔或财务报告；频率相同不代表类别相同。
_Avoid_: 数据接口数, 周期

**Quote Snapshot**:
某时点的最新成交状态与买卖盘口状态，可能包含当日累计成交量额；不代表该时点发生的一笔成交。
_Avoid_: Trade Tick, 完整逐笔

**Trade Tick**:
一笔实际成交的价格、数量、时间及可得的交易身份；只有完整序列证据才能断言覆盖全部成交。
_Avoid_: Quote Snapshot, 分钟线

**Point-in-time Availability**:
一条数据在指定历史时点已对研究者可知的性质，由发布和修订事实支持；报告期结束或今天采集到不等于当时可知。
_Avoid_: 报告期, 采集时间
