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
