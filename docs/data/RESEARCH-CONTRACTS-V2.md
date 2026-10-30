# 研究记录 v2 契约

本轮新增明确类型，供应商真实接入和扩展生命周期仍待完成。契约测试仅受控 fixture，不证明 MaiRui 提供完整 PIT、历史成员、身份变更或复权因子。可机器验证的 [JSON Schema](research-v2.schema.json) 由 `TypeAdapter(ResearchObservation).json_schema()` 生成。

## 公共边界

`qtrade_data_contracts.research` 不依赖 QTrade 账户、策略或采集数据库。所有类型冻结、拒绝额外字段；`schema_version` 为严格整数2，版本1/3、字符串"2"及布尔值拒绝。`kind` 是明确类型标签，未知类别拒绝；旧 v1 CollectionPublication/行情没有改动。

记录含作用域、来源、明确多资产标的身份、采集时刻、映射版本、可选供应商修订身份及 KnowledgeTime。采集时刻只表示本地观察，不补成历史可知时间。`content_version` 是除采集时刻外标准记录的规范摘要：同内容重复采集不产生修订；披露、指标、语义证据、映射或实际修订变更产生新摘要。后续发布清单将引用该摘要，不覆盖旧版本。

## 时间与单位

KnowledgeTime 的 `unknown` 不允许附带时刻或证据；`evidenced` 同时要求带时区的可知时刻和证据引用。披露日期是独立的 date 精度字段，不自动转换为午夜可知时刻。as-of 消费启用条件还须由能力/版本账本证据及查询层校验，模型本身不能认证完整 PIT。

UnitEvidence 的未知单位保持空；声明已知单位必须同时声明 provider/documented/local_definition 基础及证据引用。FinancialMetric 保存 original_value 和 standard_value，精确十进制文本，拒绝 float/bool/NaN/科学计数输入。原值未知必须给 provider_null/not_reported/not_authorized 原因，零值独立保存。原单位未确认时 normalization=unresolved、标准值空，不暗中用浮点类型推断元或万元。转换要求已知单位和 conversion_version，identity 要求值和单位相同。

## 类型和语义

| 类型 | 核心字段 | 拒绝/限制 |
| --- | --- | --- |
| FinancialStatement | 三表类型、report_period、disclosure_date/precision、期间语义/证据、合并范围、指标元组 | 同报告期不同披露记录各自保留；未知累计/单季不做差分；禁止重复指标及空报表 |
| MembershipInterval | universe_id、valid_from/valid_until、interval_evidence | 半开有效区间，截止必须晚于起始；采集时刻不充当成员生效时间 |
| SecurityIdentityChange | 稳定security_id、原身份、新身份、变动类型、生效日及证据 | 代码/场所变更必须有原身份，跨资产/场所身份按公共命名验证 |
| CorporateAction | source_event_id、现金/送转/拆并/配股条款、每份基数、公告/登记/除权/支付/上市各日期、状态 | 未提供日期保持空；现金必须有币种；不同类型不能混塞条款，拆并比例方向与类型一致 |
| AdjustmentFactor | 观测日、前/后/总回报口径、正因子、基准日/值、算法版本及证据 | 不把复权价格塞入因子，缺基准或算法版本不能声称已取得完整因子 |

CorporateAction 的 `event_identity` 根据作用域、来源、标的、供应商稳定事件ID计算；条款修订不改变事件身份，改变 content_version。后续Repository以二者分别去重与保留版本；仅模型计算不等于已持久化验收。

## 验证证据

`tests/test_research_contracts.py` 验证JSON往返、严格版本/类型、同报告期披露变更、重复采集摘要、未知/零、精度、单位转换、日期/可知证据、成员半开区间、原证券身份、事件身份/内容版本、拆并/配股及复权基准。`tests/fixtures/research-v1-manifest.json` 是固定受控旧清单，摘要测试及原行情固定摘要证明新模型没有改变 v1 序列化；现有独立采集归档恢复/摘要拒绝测试同时执行。

契约层阶段不改生产UI或发布接口，不提供虚构可用入口。下一步依任务2.3～2.6补发现/计划/分页、持久化/归档、门禁/发布/查询与受控适配器完整生命周期，之后三表/有限分红实接经真实UI验收。未知供应商能力维持公开限制。
