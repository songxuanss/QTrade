# Spec Delta

## Purpose

在订单进入外部交易通道前统一执行限额、市场状态、安全与审批检查，并提供熔断与 Kill Switch 保护真实资金。

## ADDED Requirements

### Requirement: 预交易检查链
每个 Order Intent 在变成 Order Command 前 MUST 通过预交易检查链，至少覆盖用户与运行时权限、策略状态、市场状态、标的许可、价格偏离、数量与精度、重复请求、频率、持仓、名义本金、杠杆、集中度、单日亏损、回撤和 Kill Switch 状态。

#### Scenario: 价格偏离过大
- **WHEN** Order Intent 价格相对参考价超过允许偏离
- **THEN** RiskEngine 拒绝该意图并返回价格偏离原因

#### Scenario: 全部检查通过
- **WHEN** Order Intent 通过所有适用检查
- **THEN** 系统生成允许的 Risk Decision 和幂等 Order Command

### Requirement: 分层限额
系统 MUST 支持 Broker Account、Strategy、Instrument 与 Workspace 维度的最大持仓、名义本金、杠杆、订单频率、集中度、单日亏损和最大回撤限额，并展示限额使用率。

#### Scenario: 账户名义本金超限
- **WHEN** 新订单将使账户名义本金超过限额
- **THEN** 系统拒绝订单并说明当前值、限额与超出数量

#### Scenario: 查看限额使用率
- **WHEN** risk_manager 打开风险面板
- **THEN** 系统按账户与策略展示各限额当前值、阈值与使用率

### Requirement: 风控决策可解释
每次 Risk Decision MUST 记录允许、拒绝或要求审批的规则版本、命中条件、输入摘要和决策时间，并可从相关订单或 Risk Event 查询。

#### Scenario: 查询拒绝原因
- **WHEN** 用户打开被风控拒绝的订单意图
- **THEN** 系统展示命中的规则、当前值、阈值和拒绝原因

### Requirement: 自动熔断
当单日亏损、最大回撤、连续拒单、异常成交、数据延迟或对账差异超过配置阈值时，系统 MUST 按配置拒绝新订单、撤销未成交订单、暂停相关策略或要求人工处理。

#### Scenario: 单日亏损熔断
- **WHEN** Broker Account 单日亏损达到熔断阈值
- **THEN** 系统拒绝该账户新订单，通知 risk_manager，并记录熔断事件

#### Scenario: 数据异常熔断
- **WHEN** 某市场行情延迟或缺口影响 Live Runtime 决策
- **THEN** 系统暂停依赖该市场的新订单，直到数据满足安全条件

### Requirement: Kill Switch
系统 MUST 提供全局和账户级 Kill Switch；启用后 MUST 拒绝所有新 Order Command，仅允许授权的平仓或管理员恢复操作，并记录操作者、时间与原因。

#### Scenario: 启用全局 Kill Switch
- **WHEN** risk_manager 启用全局 Kill Switch
- **THEN** 所有实盘新订单被拒绝，相关状态在 UI 显著展示

#### Scenario: 恢复交易
- **WHEN** 有权限用户在处理原因后关闭 Kill Switch
- **THEN** 系统记录恢复原因和审批上下文，并按配置恢复账户交易

### Requirement: 实盘审批
实盘策略发布、限额变更、Broker 凭证绑定和 Kill Switch 恢复 MUST 支持职责分离与审批；审批人 MUST 能查看风险摘要和影响范围。

#### Scenario: trader 自我审批被拒绝
- **WHEN** 同一用户同时提交并审批实盘发布
- **THEN** 系统按职责分离规则拒绝该审批

### Requirement: 交易凭证保护
Broker API 凭证 MUST 加密存储、按 Workspace 与账户授权访问，界面只显示掩码；系统 MUST 支持测试连接、轮换和撤销，并审计所有访问与变更。

#### Scenario: 查看凭证
- **WHEN** admin 查看 Broker Account 凭证
- **THEN** 系统只显示掩码与元数据，不显示完整 secret

#### Scenario: 轮换凭证
- **WHEN** admin 更新交易凭证
- **THEN** 旧凭证立即失效或按 broker 规则处理，变更进入审计日志

