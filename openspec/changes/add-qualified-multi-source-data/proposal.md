# Proposal

## Why

现有采集UI和聚合计划仍写死MaiRui来源及转换版本。近期逐笔历史短、批量来源429和目录503表明，应按数据类目、字段语义、实际权限和样本质量选源，不能用厂商接口数判断完整性。用户已提供正式版证书，必须先重新验收，不能沿用旧权限结论或认定正式版等于Pro。

## What Changes

- 建立A股研究数据分类和字段字典：主数据/日历/交易状态、原始日线与分钟线、复权/公司行为、历史行业指数、估值股本、PIT财务、行情快照、成交逐笔、委托逐笔及事件。
- 来源目录描述每类字段、单位、历史能力、权限、更新、配额、可见时间及文档/样本/端到端证据等级。
- 先验收正式版MaiRui。商业研究主源候选优先评估RQData；低成本日频候选Tushare。未有账号/权限不得显示为已接通，不购买订阅。
- 单标的与整池计划允许选择已配置、已验收来源，冻结来源/授权标识/转换版本；来源切换保留原产物，缺失覆盖按来源及语义分别计算。

## Capabilities

### New Capabilities

- `research-data-catalog`: 数据类别、字段、时点与质量条件。
- `data-source-catalog`: 实测分层的来源能力、选源和冻结来源下载。

### Modified Capabilities

无已归档主规范。与活动变更`add-portable-market-pool-collection`兼容，沿用其冻结计划、七天分页、作用域和不可变归档；不重复定义聚合运行生命周期。

## Impact

独立data-contracts/core与来源Adapter；QTrade composition、采集预检、Worker来源注册与单标的/整池UI。现有冻结研究引用不改写，新数据类别不伪装成Bar。新供应商凭证仅本地私有配置，SDK不得导入策略/交易领域。

## Non-goals and Safety Boundaries

不购买、登录新厂商账号或发送联系消息；不新增实盘。不以行情快照或成交列表冒充完整L2，不将当前行业成员用于历史有效成分，不以最新财报回填历史。完整类目字典不代表全部类别已实施。

## Unresolved Material Assumptions

RQData/Tushare账号、套餐、历史范围、吞吐、用途许可和存储许可未验收；厂商文档不是SLA实测证据。正式版MaiRui是否包含Pro必须实测；无权限时禁用对应能力。将新源加入生产前须完成真实UI/Worker/归档/行情验收。
