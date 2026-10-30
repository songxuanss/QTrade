# Spec Delta

## Purpose

管理策略从草稿到受控运行的版本、参数、校验、审批与状态转换，确保每个可执行策略版本可追溯、可复现且不可被静默篡改。

## ADDED Requirements

### Requirement: Strategy 与版本分离
系统 MUST 将 Strategy 作为生命周期主体，将代码、参数 schema、依赖环境和配置保存为不可变 Strategy Version；后续修改 MUST 创建新版本或新的待审核变更。

#### Scenario: 创建新版本
- **WHEN** analyst 修改已存在策略代码并保存
- **THEN** 系统创建新的 Strategy Version，并保留旧版本及其历史回测关联

#### Scenario: 查看版本差异
- **WHEN** 用户比较两个 Strategy Version
- **THEN** 系统展示代码、参数 schema 与依赖差异，并不改变任一版本内容

### Requirement: 参数 Schema 校验
每个 Strategy Version MUST 声明参数类型、默认值、范围和必填项；系统 MUST 拒绝缺失必填参数、类型错误或超出范围的运行请求。

一期参数 schema 使用严格 object：属性支持 string、integer、number、boolean，required、default、minimum、maximum、minLength、maxLength 和 enum；额外属性默认拒绝。

#### Scenario: 参数越界
- **WHEN** 用户提交超出声明范围的止损参数启动回测
- **THEN** 系统拒绝创建任务并指出具体参数错误

#### Scenario: 使用默认参数
- **WHEN** 用户未提供可选参数
- **THEN** 系统按 Strategy Version 声明的默认值运行，并在 manifest 中记录最终参数

### Requirement: 策略静态与安全校验
系统 MUST 对策略代码执行语法、依赖、危险访问、资源使用和基础前视偏差检查；未通过必要校验的版本 MUST NOT 进入 paper 或 live 状态。

#### Scenario: 禁止访问本地文件
- **WHEN** 策略代码尝试读取沙箱外的本地文件
- **THEN** 校验失败并列出禁止访问位置

#### Scenario: 阻止网络与未声明依赖
- **WHEN** 策略导入未锁版本的第三方包，或请求外部网络
- **THEN** 静态校验返回失败诊断和源码位置

#### Scenario: 疑似未来数据
- **WHEN** 静态检查发现策略在当前 bar 收盘前引用当根收盘价或未来时间索引
- **THEN** 系统生成警告或失败，并在校验报告中说明疑似位置

#### Scenario: 检出常见前视形式
- **WHEN** 策略对行情序列使用正向索引或使用负向 `shift`
- **THEN** 静态校验报告疑似未来数据及其源码位置

静态校验只提供预检诊断；其识别范围有限，不构成代码安全边界。所有策略执行仍 MUST 使用隔离 runner。

### Requirement: 策略状态机
Strategy Version 的可用状态 MUST 至少包含 draft、validated、backtested、paper、approved、live、paused、failed 和 archived；状态转换 MUST 受角色和前置条件限制。

#### Scenario: 未回测直接申请实盘
- **WHEN** 处于 validated 的 Strategy Version 被提交实盘审批
- **THEN** 系统拒绝该转换并说明缺少的回测或 paper 条件

#### Scenario: paper 观察不足
- **WHEN** risk_manager 审批已进入 paper 的版本，但可信 Paper 运行记录少于最低观察次数
- **THEN** 系统拒绝审批且不创建审批记录

#### Scenario: 审批只绑定一个不可变版本
- **WHEN** 已审批 Strategy 创建后续版本
- **THEN** 新版本从 draft 开始，没有继承旧版审批；旧版审批继续引用原 Strategy Version ID

#### Scenario: 状态转换历史追加保存
- **WHEN** Strategy Version 状态发生改变
- **THEN** 系统追加记录原状态、目标状态、操作者、时间、原因和证据；既有转换与审批记录不可修改或删除

#### Scenario: 归档策略
- **WHEN** admin 归档不再使用的 Strategy
- **THEN** 策略不可启动新运行，但历史版本、回测和审计仍可查询

### Requirement: 策略运行资源限制
策略校验、回测和运行时 MUST 受 CPU、内存、时长、输出大小、日志大小、网络访问和数据访问范围限制；超限行为 MUST 被记录并可查询。

#### Scenario: 内存超限
- **WHEN** 策略任务消耗超过分配内存
- **THEN** 任务被终止并标记资源超限失败，核心服务保持可用

#### Scenario: 策略输出或运行超限
- **WHEN** 策略超过运行时限、CPU、文件、进程或输出限制
- **THEN** runner 终止该子进程并返回有界诊断，API、worker 与 runner 健康端点仍可响应

runner MUST 运行在禁用网络、只读根文件系统和资源受限的独立容器中；策略源码 MUST 以不同于 runner 服务的低权限身份执行。

### Requirement: 发布审批
进入 approved 或 live 的 Strategy Version MUST 保存审批人、审批时间、被审批版本、风控配置、回测报告和回滚方案；审批记录 MUST 不可修改。

#### Scenario: 风控人员审批
- **WHEN** risk_manager 审批某个已通过 paper 验证的 Strategy Version
- **THEN** 系统记录审批人、回测运行、paper 观察数、原因和被审批版本；启动 live 仍须对应版本的执行运行时确认已活跃

#### Scenario: 审批后代码变化
- **WHEN** 策略代码在审批后发生新版本变更
- **THEN** 旧审批不自动适用于新版本，新版本必须重新完成 required 流程
