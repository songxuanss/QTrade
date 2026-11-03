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
工作台 MUST 显示当前模型未处理的复权、官方历史成员区间和逐笔盘口限制；T+1、整手、tick size 和成员区间校验已建模。当回测币种为 `CNY` 且调用方未显式提供 `execution_model`（或传空对象 `{}`）时，服务端 MUST 自动冻结 A 股现实默认参数（100 股整手、3 bps 佣金与 5 元最低佣金、5 bps 卖出印花税、5 bps 滑点、0.01 最小变动价位、T+1 交收、10% 成交量参与上限、10% 涨跌停幅度、`Asia/Shanghai` 时区与按数据集周期匹配的年化周期数），防止 API 调用静默退化为零摩擦、无夏普比率的失真结果。

#### Scenario: 日线回测
- **WHEN** 用户选择日线数据
- **THEN** 页面说明按收盘后可用，不使用盘中 session

#### Scenario: CNY 空执行模型自动补齐 A 股默认参数
- **WHEN** 调用方对 `CNY` 数据集提交回测且 `execution_model` 为空对象
- **THEN** manifest 冻结 100 股整手、T+1、佣金、印花税、滑点与 `periods_per_year`，报告产出非空的年化波动率、Sharpe 与 Sortino

### Requirement: 多标的数据集优先与跨工作台深链
数据集列表 MUST 优先返回多标的合成数据集，其次按发布时间倒序返回单标的片段，避免单标的采集分片挤占回测选择器窗口。回测工作台 MUST 支持按数据集 ID、标的代码、周期或“多标的”关键词筛选，并在选项中展示版本、标的数量、周期、起止日期与数据集短 ID；从策略工作台、数据集合成结果或研究数据集列表跳转至 `/workspace/backtests` 时 MUST 自动预选对应的 `dataset_id`、`strategy_id` 与 `version`。

#### Scenario: 合成数据集排序与直达回测
- **WHEN** 工作区同时存在大量单标的分片与多标的合成数据集，且用户从合成面板点击“前往回测此数据集”
- **THEN** 回测页数据集列表将多标的合成集排在首位，并自动选中目标 `dataset_id` 且填入其起止时间
