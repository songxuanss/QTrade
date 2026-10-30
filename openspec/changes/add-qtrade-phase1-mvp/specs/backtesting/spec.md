# Spec Delta

## Purpose

提供可复现、现实约束明确、报告标准化的回测能力，使策略研究结论可以被比较、审计和用于受控上线决策。

## ADDED Requirements

### Requirement: 回测任务创建
授权用户 MUST 能选择 Strategy Version、Market Dataset、时间范围、初始资金、参数、基准、成本模型和运行资源创建 Backtest Run；系统 MUST 在启动前冻结所有输入版本。

#### Scenario: 创建回测
- **WHEN** analyst 提交完整且校验通过的回测请求
- **THEN** 系统创建 Backtest Run、生成 manifest，并进入排队或运行状态

#### Scenario: 输入不完整
- **WHEN** 请求缺少数据集、初始资金或必要参数
- **THEN** 系统拒绝创建并返回全部可确定的字段错误

#### Scenario: 冻结回测输入
- **WHEN** 系统接受一个回测请求
- **THEN** 必须在提交引擎前生成不可变 Artifact Manifest，并将 Strategy Version、Market Dataset、规范化参数、时间范围、初始资金与币种、基准、成本模型、运行资源配置、引擎版本、环境摘要和随机种子固定在带校验和的 manifest 输入中

#### Scenario: 输入超出数据集
- **WHEN** 请求区间超出所选 Market Dataset 的可用范围
- **THEN** 系统拒绝请求，且不创建排队任务

### Requirement: 回测任务生命周期
Backtest Run MUST 以版本递增的状态记录管理，进度历史只能追加；取消和终态更新必须遵守引擎状态，运行输入与完成结果引用不可被覆盖。

#### Scenario: 更新任务进度
- **WHEN** 引擎报告新的合法进度
- **THEN** 当前状态以比较并交换方式更新，进度历史追加带递增版本号的事件

#### Scenario: 结果产物不可变
- **WHEN** 引擎报告回测成功
- **THEN** 结果对象必须已存在于同一 Workspace 且校验通过；结果引用写入后不能修改或替换

#### Scenario: 取消终态任务
- **WHEN** 用户取消已完成、失败或已取消的 Backtest Run
- **THEN** 系统拒绝状态变更并保留已有历史

### Requirement: 现实交易约束
回测 MUST 应用手续费、滑点、延迟、最小交易单位、交易时段、可成交性、部分成交和指定市场的价格限制约束；这些约束 MUST 在报告中展示。

#### Scenario: 应用手续费与滑点
- **WHEN** 回测生成一笔成交
- **THEN** 成本模型计算手续费与滑点，权益曲线与交易明细体现该成本

#### Scenario: 涨跌停约束
- **WHEN** 目标市场规则禁止在某价格状态下成交
- **THEN** 回测按规则拒绝或延迟该交易，并在交易明细中记录原因

### Requirement: Mock 引擎事件回放
MockEngineAdapter MUST 对冻结的数据按时间与标的稳定排序，将策略信号作为事件处理，并通过标准结果转换器输出可校验的不可变产物。Mock Adapter MUST NOT 在 API 或自身进程中执行用户提交的 Python 源码。

#### Scenario: 同一输入重复运行
- **WHEN** 两次运行使用同一 Strategy Version、Market Dataset、参数和随机种子
- **THEN** 订单、成交、权益曲线、汇总结果及其规范化内容哈希一致

#### Scenario: 当前 bar 信号成交
- **WHEN** 策略在某根 bar 事件后发出订单信号
- **THEN** 模拟订单只能在该标的后续 bar 开盘价成交，或在数据结束时明确标记未成交

#### Scenario: 未注册的 mock 策略
- **WHEN** MockEngineAdapter 没有该 Strategy Version 对应的确定性测试程序
- **THEN** 引擎返回受控错误，不执行或猜测性解释策略源代码

### Requirement: 标准绩效报告
每个完成回测 MUST 输出权益曲线、现金曲线、回撤曲线、持仓、订单、成交、交易明细、基准对比和至少包含总收益、年化收益、波动率、Sharpe、Sortino、Calmar、最大回撤、胜率、盈亏比、换手率与暴露的指标。

#### Scenario: 查看报告
- **WHEN** 用户打开完成的 Backtest Run
- **THEN** 系统展示标准指标、曲线、交易明细和成本假设

#### Scenario: 指标样本不足
- **WHEN** 某指标因样本过短不可计算
- **THEN** 报告显示不可计算及原因，不使用默认值伪装结果

### Requirement: 回测可复现
在策略、数据、参数、引擎、环境和随机种子声明一致的情况下，Backtest Run MUST 产生一致的订单、成交、权益曲线与指标；非确定性来源 MUST 显式记录。

#### Scenario: 重复运行
- **WHEN** 用户用同一 manifest 重复提交回测
- **THEN** 两次结果的核心指标与交易明细一致

### Requirement: 任务进度与失败
回测任务 MUST 展示排队、运行、进度、取消、失败和完成状态；失败必须保留错误、日志摘要和已完成的安全部分结果。

#### Scenario: 用户取消任务
- **WHEN** 授权用户取消运行中的回测
- **THEN** 系统停止任务、释放资源，并将任务标记为已取消

#### Scenario: 引擎异常
- **WHEN** 引擎 runner 崩溃或失去响应
- **THEN** 任务在超时后标记失败，API 与 worker 保持可用

### Requirement: 实验对比
授权用户 MUST 能选择多个已完成 Backtest Run 进行并排比较，并查看输入差异、指标差异、权益曲线与交易分布。

#### Scenario: 比较两个参数版本
- **WHEN** 用户选择同一 Strategy 的两个参数回测
- **THEN** 系统展示参数差异和核心指标对比

#### Scenario: 混合不可比数据
- **WHEN** 用户选择数据范围或市场明显不同的回测
- **THEN** 系统仍可展示差异，并明确提示不可直接作为同基准比较

### Requirement: 基础 Walk-forward
系统 MUST 支持至少一种显式训练/验证时间切分，并在报告中展示每段边界、样本数与指标；切分 MUST NOT 允许验证段数据泄漏进训练输入。

#### Scenario: 运行 walk-forward
- **WHEN** 用户按日期配置滚动训练与验证窗口
- **THEN** 报告按窗口展示结果和汇总指标，且窗口边界可追溯
