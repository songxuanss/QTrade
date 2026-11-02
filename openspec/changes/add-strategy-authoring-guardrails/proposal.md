# 提案

## Why

策略研究目前只有手写 Python 一条入口：新用户不知道 `qtrade.Strategy` 的契约，参数 schema 需要手写 JSON 且字段名容易与 `self.parameters.get()` 脱节，静态校验错误只出现在列表里、无法定位到具体代码行。结果是创建策略的门槛高，且容易写出时序或因果错误。

## What Changes

- 提供官方策略模板库（单标的均线、RSI 反转、横截面动量、低波、等权基准），每个模板包含可运行源码与参数 schema，一键填充到工作台。
- 将参数 schema 从纯手写 JSON 改为结构化表单编辑，保留 JSON 视图；字段名、类型、默认值、范围、枚举与必填项可视化维护。
- Monaco 编辑器注册 `qtrade` API 补全与悬停文档，覆盖 `Strategy`、`on_start`、`on_bar`、`order`/`buy`/`sell`、`parameters` 与 `bar` 字段。
- 静态校验诊断以 Monaco marker 内联显示（行/列），保留现有底部诊断列表。
- 执行模型不变：模板生成的源码仍冻结为不可变 Strategy Version，仍只能通过隔离 Runner 执行。

## Capabilities

### New Capabilities

- `strategy-authoring`: 模板库、参数 schema 表单编辑器、编辑器补全与内联诊断。

### Modified Capabilities

无。

## Impact

`apps/web` 策略工作台与样式、`apps/web/tests/unit`、`tests/test_strategy_templates.py`。不涉及 API、Worker、Runner、回测执行或数据库结构。

## Non-goals and Safety Boundaries

- 不引入可视化 DAG 构建器或第二套执行语义。
- 不安装第三方 Python 依赖，不放宽 Runner 沙箱、静态校验规则或资源上限。
- 不改变策略版本生命周期、manifest、参数解析或回测执行。
- 模板只提供初始源码与 schema；用户保存后以冻结源码为准，模板不参与运行时。

## Unresolved Material Assumptions

横截面模板依赖多标的日线数据集与“决策日次日成交”的统一时序；参数默认值面向 A 股日线与 100 股整手，其他市场需自行调整。模板不携带官方历史成员区间，也不宣称模板收益代表未来表现。