# 设计

## 执行边界

API 只持有 Runner UDS 客户端，不在 Web/API 进程执行用户源码。`IsolatedBatchStrategyProgram` 把有序 bar 序列、参数和 seed 发送给 Runner；Runner 子进程按顺序调用 `on_bar`，返回每个 bar 的订单提案。`MockEngineAdapter` 只消费索引化提案并按事件顺序撮合。

## 冻结与恢复

Manifest 绑定 `strategy_version_id`、`code_sha256`、`dataset_sha256`、规范化参数、执行模型和 seed。回测读取源码时按 manifest hash 与对象存储比对；报告读取时校验 run、manifest、币种、seed、样本数与期末权益。

## 执行语义

- 信号在后续 bar 的 open 成交；日线不套用盘中 session。
- 最终成交数量在现金/成交量约束后再次按 lot 向下取整，避免零股。
- 费用、滑点、成交量上限、价格限制和周期年化频率来自冻结执行模型。
- 指标以初始资金为起点，首期损失进入回撤和年化。

## UX

策略默认模板是可运行的 SMA 交叉模型；参数从版本 schema 生成表单。运行列表自动刷新并显示策略/数据集名称；报告对指标保留两位小数并展示参数与哈希；对比支持显式选择 2–20 个完成运行。

## 已知缺口

已实现 T+1 可卖数量、tick size、最低佣金、卖出印花税、持久化 Worker 队列、公司行为账本和 Walk-forward 逐窗执行/汇总；未实现配股、供应商复权因子、历史有效成分、逐笔盘口和报告导出。页面明确提示模型边界。
