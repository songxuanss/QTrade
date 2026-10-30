# 新鲜度异常事件的原子持久化

本阶段实现 OpenSpec 8.2 的不可变 outbox 事实与去重状态，不是完整告警交付。公开投递、QTrade 站内通知消费、自动刷新与真实异常恢复链路尚未接通，保持17/42，8.2不勾选。

## 行为与接口约束

FreshnessNotice是独立共享v1契约，携带作用域、操作主体、完整核验结果、episode/notice确定性UUID和开启/发生时间。scope/时钟/ID/状态不一致拒绝，不能用unknown、within_grace或来源状态未知的current记录伪造恢复。episode按完整key和首次异常UTC时刻形成，opened/recovered各有稳定事件ID，后续消费可用于幂等。

核心transition_notice读取最近事件状态。首次late/unavailable开启；持续异常、未知、宽限不重复、不恢复；current且source_health=available追加关联恢复；后续再次异常形成新episode。恢复要求额外验证来源可用，避免仅凭已发布覆盖就声称来源故障消失。该运维事件规则与单份数据集覆盖状态有意区别。

FreshnessRefresh在已授权的原子key事务中合并保存快照后notify，事件插入失败则快照及事件一起回滚。replayed/superseded不进入事件写入；普通精确历史append/record不产生运维事件。notify仅在bound连接/正确key/刚保存的最新结果下可用，不进行供应商或通知网络I/O。

追加0017迁移，不修改0001–0016。outbox事实包括摘要、scope/key、episode/kind、关联snapshot FK及递增序列；数据库拒绝UPDATE/DELETE/TRUNCATE。读取复核严格共享模型、内容摘要和关联快照身份，非法限额或错作用域拒绝/空结果。此表仅证明待投递事实，尚无投递完成字段或确认，不应直接手改或清空。

## 受控验证

`tests/test_freshness_notice.py`与`tests/test_freshness_notice_postgres.py`覆盖开启→重复→未知/宽限→恢复→新异常、错scope/ID/时钟、UTC等价身份、来源未知不误报恢复、12并发相同检查只保存一个快照/一个事件、写入后故障整笔回滚、独立notify拒绝、跨key拒绝、原始事实不可变及损坏内容拒绝。

使用临时唯一名隔离PostgreSQL库，应用真实迁移并清理仅自有测试库；连同原新鲜度/HTTP/研究发布回归148项通过。另契约/架构/会话桥接分组通过；Ruff/Mypy和OpenSpec严格校验通过。以上异常、日历和恢复为受控输入，不能计作供应商真实故障证据。

## 部署验证

collector镜像 `sha256:de80d654d39a93a98f82761081f4aa743371eddc26e282112c417f18a00ab612` 已部署并健康，0017迁移实际应用且校验和与源文件一致；0001–0016保持原摘要。Web/API沿用上一阶段已部署版本，未改UI代码。

`cd apps/web && node tests/verification-freshness-ui.mjs`：原真实SSO/UI→3122会话API→18100→PG事务核验通过。实际管理员1440桌面键盘重新核验、保存后刷新及只读390手机读取一致，旧年度记录仍为unknown；只读管理按钮隐藏/直接POST403、缺CSRF/跨空间/匿名拒绝、受控clock过期、读/核验传输失败和恢复、axe0及无溢出通过。受控传输故障不是供应商实际故障，不能计作真实异常通知恢复。

`.venv/bin/python scripts/verification-freshness-notice.py`：三次真实年度任务中显式定位601288.SH，核对3份发布/1个逻辑key，当前授权回查及真实PG快照均unknown，outbox为空，没有把未知历史伪造成过期/恢复事件；0017校验和验证通过，无供应商调用。首轮脚本误取每个任务首行导致后两次选中不同股票的无快照记录，检查明确失败；改为固定目标证券后通过，未替换失败的供应商数据。

本轮复用原真实下载，不新增价格探测或通知承诺。`.venv/bin/python scripts/verification-daily-freshness-evidence.py`再次通过三份真实年度发布（各243行）的raw/standard/质量重放及公开快照读取；snapshot_written=false，没有另写测试状态。详细结果见私有review目录。新增outbox的受控过期/恢复用例与部署真实unknown路径分别记录。

## 仍待完成

实现有界可恢复投递及当前授权，按同key顺序消费、稳定ID幂等，通知持久化后确认；接入现有项目告警和站内通知，验证重复过期/重启/撤权/恢复。连接持久自动刷新及其他类别规则、公开健康视图，再完成实际桌面/手机告警全链。不得用本次outbox行数、fixture或queued状态勾选8.2。
