# 新鲜度持久快照及只读查询

本阶段完成OpenSpec 8.1的存储/查询基础分组，8.1–8.3均仍未完成，整体17/42。真实证据生产器、自动刷新、QTrade桥接/UI和告警outbox未接通；没有向实际部署注入健康fixture来伪装新鲜度闭环。

## 实现

0016追加collection_freshness_snapshot，按scope/完整key摘要/checked_at唯一，保存不可变结果、内容摘要和确定性snapshot ID。UTC规范化检查、预计发布及观察时刻，同一瞬间的不同offset表示幂等；相同身份和时刻的不同结果冲突，不覆盖。最新按checked_at而非落库时刻排序，旧探测晚完成不替代新检查。UPDATE/DELETE/TRUNCATE触发器拒绝，SQL约束使用IS TRUE防缺字段NULL绕过及schema布尔伪造；原0015及更早迁移不改。

PostgresFreshness每次读重新解析严格结果，校验key、scope、时间、完整内容摘要和确定性ID；不因已在库中就信任内容。历史按检查时刻递减、严格before游标，单次1–100；错checksum/身份fail closed。

FreshnessSnapshots独立core端口在读/写前检查当前read/manage，拒绝model_copy构造的非法写输入，并校验仓储返回的身份、状态、排序、上限和游标。写入确认不能返回另一结果。业务API无写快照端点，浏览器不能自报健康状态。

collector新增read-only POST `/v1/freshness/latest`（body为FreshnessKey）和 `/v1/freshness/history`（FreshnessHistoryQuery：key、limit、before），使用现有服务Bearer与X-Data-Actor/Scope及当前授权回查，body key作用域必须与subject一致。POST承载严格结构化查询，不是状态命令。成功no-store；查询无供应商I/O，不自动探测、发布或花费来源信用。

返回的是checked_at时刻的历史判定，不是此刻重新计算的健康值；生产刷新及UI必须展示检查时间并处理快照过旧。无快照latest404、history空数组，不能推导为current。新增共享HistoryQuery及schema，不改冻结发布/manifest和已提交五份新鲜度schema。

## 测试证据

89项隔离真实PG/领域/认证HTTP/既有研究元数据回归通过，无跳过。数据库唯一research_test_随机名，完整迁移后测试，仅删除该自建数据库。

- 故障/恢复历史、仓储重开、旧探测晚到；8线程等价offset竞争同一记录；同钟不同结果冲突。
- scope/source/标的/调整/映射各自隔离；SQL更新/删除/清空拒绝，缺scope/key/evidence或schema布尔拒绝；坏摘要/快照ID拒绝。
- READ可读不可记录、跨scope拒绝、撤权后再次查询拒绝；拒绝发生在仓储之前。异常port返回别的数据集、乱序/重复/超量/越过游标或错误写确认不返回给调用者。
- 真实FastAPI subject、服务身份和当前角色→真实PG查询，匿名401、错token401、body/头跨scope403、额外actor及非法limit/无时区cursor422、缺快照404、无公开写路由；撤权403。spy证明查询不触发provider.default_range/fetch。受控记录不计供应商探测。

测试扩展中4个SQL约束用例曾因json导入被早先lint移除而NameError，补回后执行通过；HTTP测试最初在配置前导入全局app导致收集KeyError，移入env准备后重新运行通过。两者均为测试准备问题，不作为供应商故障或通过证据。

Ruff/format和四模块Mypy通过，OpenSpec严格校验通过。

## 实际部署

collector镜像 `sha256:b32fe6adce789ebfda39d70d4fa612cdc18f14f6164f2159afc17b384628b172` 已构建启动且ready成功，迁移0016随服务启动应用。Web仍13000，认证API3122，独立服务18100，旧持久卷不变。

`verification-freshness-read.py`分别用已有专用非实盘空间的admin/reader真实主体与服务授权验证：latest404、history[]且no-store、body跨scope403、bool limit422、匿名401。没有写入业务快照、伪造current或调用来源；这只证明已部署查询/授权和明确缺失，不是新鲜度成功集成。

部署后重验30个既有非空发布的raw/standard/源回执/门禁/公开分页/实际消费者摘要通过。真实财务income新任务6披露版本、公司行为新任务31条事件，桌面/手机axe、浏览/刷新通过，精确UI-created publication再次读取部署卷的非空raw/standard和版本/摘要一致。主成功回归不替代新鲜度UI验收。

命令：私有隔离DB环境运行 tests/test_freshness_routes_postgres.py、test_collector_http.py、test_freshness_store.py、test_collection_freshness_postgres.py、test_collection_freshness.py、test_research_evidence_postgres.py；`QTRADE_FRESHNESS_ACTOR=admin|reader PYTHONPATH=packages/data-contracts/src .venv/bin/python scripts/verification-freshness-read.py`。财务/事件UI及精确归档核验命令沿用既有脚本。报告/私有JSON不包含配置、凭证或供应商URL。

下一阶段必须接入真实覆盖/原始/发布证据生产与有界刷新，之后桥接UI和持久告警过期→去重→恢复链路；原完整Goal与未完成任务保留。
