# Spec Delta

## Purpose

提供高信息密度、低延迟、可保存的专业分析与监控工作台，让用户能同时理解行情、组合、数据和系统状态。

## ADDED Requirements

### Requirement: Market Watch
授权用户 MUST 能在 Workspace 中维护自选 Instrument 列表，查看实时或最近价格、涨跌幅、成交量、数据延迟与市场状态，并保存列配置和排序。

#### Scenario: 实时更新自选列表
- **WHEN** 用户打开 Market Watch 且对应行情连接正常
- **THEN** 价格、涨跌幅和成交量在无需手动刷新的情况下更新，并显示最后更新时间

#### Scenario: 保存列配置
- **WHEN** 用户调整列显示、排序和分组后保存工作区
- **THEN** 下次进入同一工作台时恢复相同配置

### Requirement: 专业 K 线图
系统 MUST 提供带成交量、时间缩放、周期切换和基础指标的 K 线图，并能在授权数据范围内查询历史；数据缺口与质量异常 MUST 可见。

#### Scenario: 切换周期
- **WHEN** 用户在 1m、5m、1h、1d 等可用周期之间切换
- **THEN** 图表加载对应标准数据，并保持当前标的与时间上下文

#### Scenario: 展示数据缺口
- **WHEN** 图表区间存在已检测的数据缺口
- **THEN** 图表或状态条展示缺口提示，用户可跳转到对应 Data Quality Issue

### Requirement: 基础指标计算
系统 MUST 至少支持移动平均、指数移动平均、MACD、RSI、布林带和 ATR 的服务端计算，并显示参数、样本窗口与数据不足状态。

#### Scenario: 添加指标
- **WHEN** 用户为图表添加指定参数的指标
- **THEN** 系统基于标准行情数据计算并叠加展示结果

#### Scenario: 样本不足
- **WHEN** 指标窗口大于可用数据长度
- **THEN** 系统显示数据不足提示，不输出误导性部分指标值

### Requirement: 组合与风险视图
至少在 Paper/Live Runtime 存在持仓或订单时，工作台 MUST 展示账户权益、现金、持仓、盈亏、暴露、集中度、回撤与未完成订单。

#### Scenario: 查看账户风险摘要
- **WHEN** 用户打开组合视图
- **THEN** 系统按账户与策略展示权益、现金、持仓、未实现盈亏、暴露与回撤状态

#### Scenario: 风险限额接近阈值
- **WHEN** 某个账户或策略的风险限额使用率超过告警阈值
- **THEN** 工作台高亮该状态并链接到对应 Risk Event

### Requirement: 数据与系统健康面板
工作台 MUST 展示数据源连接、采集任务、队列、关键服务、Broker 连接和近期告警的健康状态；异常状态 MUST 能跳转到详情。

#### Scenario: 采集任务失败
- **WHEN** 某个市场数据采集任务失败
- **THEN** 健康面板显示失败市场、任务与影响，并链接到错误详情

### Requirement: 命令面板与快捷键
系统 MUST 提供命令面板和核心快捷键，支持按权限搜索并跳转 Instrument、Strategy、Backtest Run、Order、Alert 与管理页面。

#### Scenario: 搜索并打开标的
- **WHEN** 用户通过命令面板输入 Instrument 名称或代码
- **THEN** 系统展示匹配结果并在选择后打开对应图表或详情

#### Scenario: 无权限结果
- **WHEN** 某资源不属于用户可访问的 Workspace
- **THEN** 命令面板不返回该资源的可操作结果

