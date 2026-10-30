# 真实日线发布的新鲜度证据

本阶段完成8.1的发布证据验证分组，并在现有专用非实盘空间用真实归档产生一笔unknown快照、经公开接口查询。自动生产/有界刷新、UI、告警仍未装配；8.1–8.3保持未完成，整体17/42。

## 验证模块

DailyPublicationEvidence对当前READ主体按scope/publication ID读取真实已完成分段，复核DB与不可变文件manifest一致。当前明确支持CN、日线、本地交易日标签及未复权；分钟、其他市场、复权、超过20000原始或标准行拒绝，不通过套用A股时区伪装支持。

新增FileCollectionArchive.raw_records重开准确raw Parquet并校验字节SHA/数量/结构；source_captures按scope/part/job/actor/source/mapping和确定性ID校验回执及原始JSON字节SHA/数量，最多100份回执、累计64MiB。只考虑质量检查之前的获取证据；后续重试不能追溯证明早期产物。读取不向调用者公开正文、URL、头或lease。

模块重放原始/标准记录、单位、时区/时间标签、来源evidence与映射的现有质量规则，要求决策完全相同。相同记录哈希不能代替不同单位或来源语义的检查。没有匹配门禁或获取回执则拒绝。

日周期checkpoint还要求实际valid日线、当地零点标签、请求覆盖该完整自然日、日期在来源日历内，且不晚于发布日；半天请求、可疑线、不对齐标签不能认证。complete_through表示最后一个满足这些条件的日周期，不表示全部历史缺口已修复、价格绝对准确或研究就绪。采集时间来自已核验回执，发布时间来自原manifest；证据摘要绑定原发布、检查及回执。

验证器输出已发布周期及其血缘，不把历史下载当作来源最新探测；它不自建定时任务或覆盖已有新鲜度结果。关键边界与原因均有注释。

## 测试和实际结果

63项证据/既有采集/质量测试通过，包括真实文件重开、后续回执不改旧证明、半天/可疑/非日标签拒绝、同记录哈希单位变化不能复用旧门禁、原始/回执损坏/身份错误或缺检查拒绝、撤权/跨scope及不支持形态在打开归档前拒绝。受控样本是fixture，不计真实来源成功。

collector镜像 `sha256:421e2622c34fc2a7fb69423eff4033e0f6197544db39b5af840077e7b22b27d4` 已构建部署。真实1/5/20年度复测各自独立601288.SH发布均通过原始/标准/获取回执/质量重放，最后日周期为2025-12-31，采集不晚于发布，摘要可重新计算。

`verification-daily-freshness-evidence.py`仅在既有专用非实盘验证空间操作：三份真实证据选择最高周期/最新发布，现有当前授权与共享用量下获取来源日历/预计发布规则；无快照时通过FreshnessSnapshots.record生成首笔真实证据快照，已有快照仅查询、不覆盖。此脚本不是通用并发刷新器或正式写API，不据其通过宣称自动生产已接入。

本次首笔快照实际写入：预期截至2026-10-14、已发布完整周期2025-12-31，source checkpoint缺失、source_health unknown，结果unknown/source_unknown；不能从旧范围缺少新日期推断来源不更新或本地采集滞后。已确认来源日历不等于已探测当前价格。通过实际服务latest读取，严格结果与写入一致。生产价格未重新请求、没有伪造原始/标准数据或健康fixture。

管理员和只读真实主体随后均通过公开latest/history读取、作用域/匿名/严格limit拒绝；读验证脚本现在按实际持久状态核验有效快照或明确缺失，避免一直假设历史为空。历史记录、归档与已有消费者未删除。

首次fixture用错日线quality枚举，文件重读拒绝；改为合法valid后相关测试通过，未放宽生产校验。控制脚本最初从stdin运行：有界HTTP spawn子进程未正常产出结果，安全诊断为TransportFailure/unavailable；改为独占0600真实临时文件及__main__保护后通过并清理文件。没有改证书、绕预算或将该准备失败推断为供应商权限/历史问题。报告输出曾遗留旧snapshot_written包装，已按实际子结果改正；最终私有报告明确snapshot_written=true、public_query_verified=true。

Ruff/format、两模块Mypy及OpenSpec严格校验通过。私有报告仅保存可公开的身份/周期/检查时间/摘要和状态，不包含配置/证书/供应商URL；guarded临时文件只含代码和非秘密身份，凭证运行时从原配置读取。

命令：`.venv/bin/python -m pytest tests/test_daily_freshness_evidence.py tests/test_collection_quality.py tests/test_independent_collection.py`；`.venv/bin/python scripts/verification-daily-freshness-evidence.py`；admin/reader分别运行verification-freshness-read.py。部署后30个既有非空发布的raw/standard/源回执/门禁/分页及消费者摘要通过；实际SSO UI再读1/5/20组任务，精确601288发布图表和刷新通过。此为既有主链回归，不是新鲜度UI验收。

## 保留的完整目标

需实现可并发、有界、持续的证据刷新及正确的全数据集checkpoint合并，不能让检查旧发布覆盖较新数据的状态；来源实际最新观察、采集/门禁阻断阶段、其他类别更新规则和故障仍须接通。随后完成业务桥接/UI与持久告警/恢复链路，包含桌面手机、撤权、故障、去重及重启。当前没有新鲜度页面，CLI/API分组通过不能代替新鲜度UI主链验收。
