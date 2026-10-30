# 新鲜度证据与阶段规则

本阶段实现OpenSpec 8.1的独立契约及纯领域判定，8.1/8.2/8.3均保持未完成，整体17/42。尚未连接持久快照、公开查询、部署UI、告警或供应商探测，不将受控测试称为真实新鲜度闭环。

## 模块与语义

新增FreshnessKey/Expectation/Checkpoint/Evidence/Result及五份独立v1 JSON Schema，不修改旧发布、清单或原始manifest。key隔离scope/source/标的/类别/字段/日频/日期轴/调整口径/映射版本，不跨作用域或口径混用。当前日频行情/因子及财务报告期/事件日期有明确身份类型；分钟/逐笔未定义新鲜度策略，不接受为日频。

Expectation来自来源日历及预计发布时间，不能用本地最大数据日期决定预期截止。daily_expectation只允许行情，使用既有DefaultRange的日历last/expected_available_at和来源依据；它是预计发布，不是供应商已更新证明。因子与稀疏财务/事件必须由各自有证据策略给出expectation，没有规则则unknown，不能套周一到周五或每日必有事件的假设。

Checkpoint.complete_through是按适用类别/期间覆盖规则验证的完整时段，附观察时间和稳定证据摘要；不是任意分页的最大timestamp。生产适配器必须取得真实证据后才可构造；原始HTTP成功、任务completed、最新日期或摘要字段本身不能证明完整。最新周期新鲜不等于全部历史覆盖或研究就绪，历史缺口另行展示。

判定规则：

| 条件 | 状态/阶段 | 操作 |
| --- | --- | --- |
| 来源不可用，即使缓存发布看似截至最新 | unavailable/source | 检查来源 |
| 无类别发布时间规则 | unknown/unknown | 核验更新规则 |
| 已验证发布达到预期完整时段 | current/none | 无补采动作 |
| 发布未达预期且来源观察未知 | unknown/unknown | 检查来源，不猜测本地滞后 |
| 有证据来源完整时段落后 | late/source或within_grace/source | 检查来源 |
| 来源已更新，本地原始覆盖未达到 | late/collection或within_grace/collection | 补缺 |
| 原始完整时段已取得，但发布未达到 | late/publication或within_grace/publication | 检查门禁/发布 |

current描述预期数据的已发布覆盖，仍原样携带source_health unknown与缺失source checkpoint；不能把它宣称为实时来源连接健康。明确unavailable总是优先显示，不被缓存遮蔽。

配置grace_seconds严格整数0–604800，预计发布加宽限之前within_grace、恰到边界late且逾期秒数0；未知/不可用不捏造逾期时长。截止与时区明确，拒绝未来检查证据、未来完成日期、未来预计发布、无时区检查时钟。公开结果验证状态/阶段/动作与证据及宽限时钟一致，不允许无发布证据的current。

## 验证

46项新鲜度/既有默认日期测试通过：来源、本地、发布滞后；无来源观察/规则未知；缓存不遮来源故障；宽限边界；权威fixture日历排除工作日节假日、周末及发布前后；稀疏因子/财务/事件不继承日线规则；跨scope/source/标的/调整/映射拒绝；非法key/规则/未来证据/无时区/状态伪造拒绝；schema接受真实生成结果、拒绝未知版本和额外URL字段。日期计算只使用有界受控日历，这不是供应商实时日历或UI证明。

`.venv/bin/python -m pytest tests/test_collection_freshness.py tests/test_collection_default_range.py`：46 passed；Ruff与两模块Mypy通过。`PYTHONPATH=packages/data-contracts/src .venv/bin/python scripts/export-collection-freshness.py`只导出新增五份schema。

首次新增拒绝测试因json局部导入范围导致3个NameError，移至模块导入后全部通过；没有把未执行断言计通过。注释解释覆盖证据、来源预计与实际、缓存及未知类别限制。

## 后续必须完成

连接真实覆盖/归档/发布证据读取与可查询持久快照，保留已有数据截至、采集/发布时间及预期；实现当前授权公开接口与作用域隔离。随后部署UI及角色/桌面/手机/外部故障验证，完成持久告警outbox与项目告警过期→去重→恢复主链。规则通过不计完成8.1，原目标不缩减。
