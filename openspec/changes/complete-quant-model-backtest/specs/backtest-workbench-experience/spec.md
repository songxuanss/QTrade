# 回测工作台体验

## Purpose

让用户从策略版本、参数、数据集到报告和对比的流程可发现、可解释。

## ADDED Requirements

### Requirement: 可运行默认模型与参数表单
新建策略 MUST 提供可运行的示例模型；回测表单 MUST 按冻结版本 schema 渲染参数，不要求手写 JSON。

#### Scenario: 使用默认模型
- **WHEN** 用户新建默认策略并完成校验
- **THEN** 回测表单显示 fast/slow/quantity 默认值并可提交

### Requirement: 可读运行状态
运行列表 MUST 显示策略名、版本、数据集和周期，运行中自动刷新或提供刷新操作。

#### Scenario: 运行完成
- **WHEN** 运行从 queued/running 变为 completed
- **THEN** 列表最终显示完成和报告入口，不需要重新登录

### Requirement: 报告与对比
报告 MUST 显示参数、代码/数据摘要、引擎、seed 和格式化指标；用户 MUST 可选择 2–20 个完成运行对比。

#### Scenario: 对比参数实验
- **WHEN** 用户勾选两个完成运行
- **THEN** 系统打开对比页并提示输入差异与指标

### Requirement: 明确模型边界
工作台 MUST 显示当前模型未处理的复权、T+1、历史成分和逐笔盘口限制。

#### Scenario: 日线回测
- **WHEN** 用户选择日线数据
- **THEN** 页面说明按收盘后可用，不使用盘中 session
