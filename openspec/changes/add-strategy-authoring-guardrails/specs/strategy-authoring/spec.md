# 策略创作

## Purpose

让用户在不知道策略接口细节的情况下，也能快速得到可运行、可校验、可回测的策略版本。

## ADDED Requirements

### Requirement: 官方模板库

工作台 MUST 提供至少五个官方策略模板，覆盖单标的趋势、单标的反转、横截面动量、横截面低波与等权基准。每个模板 MUST 同时提供可运行源码与参数 schema，并允许用户一键填充后继续编辑。

#### Scenario: 从模板创建策略
- **WHEN** 用户在新建策略时选择一个模板
- **THEN** 编辑器填入该模板源码，参数 schema 与参数表单同步更新，策略名称得到可编辑的默认值

#### Scenario: 模板源码可执行
- **WHEN** 仓库测试读取模板清单
- **THEN** 每个模板源码通过服务端静态校验，参数 schema 通过规范化校验

### Requirement: 参数 schema 表单编辑

工作台 MUST 允许以表单维护参数名、类型、默认值、范围、枚举、必填与说明，并 MUST 保留 JSON 视图用于查看与高级编辑。两种视图 MUST 产出同一份规范化 schema。

#### Scenario: 表单新增参数
- **WHEN** 用户新增一个整数参数并设置默认值与最小/最大值
- **THEN** JSON 视图出现对应属性，回测表单可以渲染该参数

#### Scenario: JSON 解析失败
- **WHEN** 用户在 JSON 视图输入非法 JSON 并切回表单
- **THEN** 界面保留原文并提示错误，不静默清空或覆盖用户输入

### Requirement: 编辑器接口提示

编辑器 MUST 为 `qtrade` 策略接口提供补全与悬停说明，覆盖 `Strategy`、`on_start`、`on_bar`、`order`、`buy`、`sell`、`parameters` 与常用 bar 字段。提示 MUST NOT 替代服务端校验。

#### Scenario: 查询下单方法
- **WHEN** 用户将光标停在 `buy` 或 `order` 上
- **THEN** 显示方法签名、数量与订单类型约束

### Requirement: 内联静态诊断

静态校验 MUST 将诊断按行内联到编辑器，并保留列表视图。代码变更或重新校验时 MUST 清除过期标记。

#### Scenario: 校验发现禁止能力
- **WHEN** 用户在策略中调用被禁止的能力
- **THEN** 对应行出现错误标记，列表显示诊断码与说明

### Requirement: 执行语义不变

模板与表单编辑 MUST NOT 改变策略执行方式。保存后的版本仍为不可变 Strategy Version，仍只能由隔离 Runner 执行；模板元数据 MUST NOT 参与运行时或 manifest 复现判断。

#### Scenario: 模板策略回测
- **WHEN** 用户用模板创建并校验策略后提交回测
- **THEN** 系统按既有冻结源码、参数与数据集执行，并生成同样的 manifest 字段