# 提案

## Why

回测页面此前只创建排队记录，未执行真实行情回放；默认策略是空 `pass`，参数只能手写 JSON，运行状态不会自动刷新，报告和运行列表用 UUID 片段标识对象。用户无法判断某次回测是否真的运行了量化模型。

## What Changes

- 将存储的 Python Strategy Version 通过隔离 Runner 执行，按 bar 顺序调用 `on_start`/`on_bar`，信号只作用于当前及历史数据。
- 本地组合使用事件回放引擎读取真实 Timescale 数据集，Manifest 冻结代码、数据、参数、费用、滑点和 seed。
- 日线跳过盘中 session；最终成交按 lot 取整；绩效从初始资金计算首期损失。
- 默认模板改为 SMA(5,20) 交叉模型；回测表单按版本 schema 渲染参数，运行历史显示策略/数据集名称。
- 运行中自动刷新，可选择 2–20 个已完成运行对比；报告展示参数、代码/数据摘要、引擎和 seed。
- 报告读取校验结果与 run/manifest/seed/样本数一致。

## Capabilities

### New Capabilities

- `quant-model-execution`: 策略源码隔离执行、版本与参数冻结、事件回放、成交与绩效语义。
- `backtest-workbench-experience`: 参数表单、运行状态、报告、对比和模型限制说明。

### Modified Capabilities

- `backtesting`: 真实执行、结果完整性和 UI 可解释性。

## Impact

apps/runner、adapters/runner、adapters/mock、packages/application/backtesting、apps/api 组合、Web strategy/backtest UI、本地部署脚本、Python 与浏览器测试。

## Non-goals and Safety Boundaries

不实盘下单，不删除旧运行，不宣称未实现的复权/历史成分/逐笔盘口能力；T+1、tick size 和持久队列已实现。无 Runner 时回测明确失败，不静默使用占位结果。

## Unresolved Material Assumptions

公司行为、历史有效成分和逐笔盘口尚未实现；T+1、tick size 和持久 Worker 队列已实现。当前 Runner 以 Unix socket 和子进程限额隔离，多 Worker 公平调度仍需扩展。
