# 自动执行批准的当前计划重验

本阶段推进7.1/7.3内部批准检查，整体14/42，未接通定时/UI/自动来源；不计自动采集交付。

## 实现语义

CollectionService.submit新增内部keyword-only automatic_approval，类型为ScheduleExecutionApproval，持有不可变严格CollectionScheduleDraft。它不在公开CollectionJobRequest或HTTP提交字段中，普通手工确认行为保持。当前只支持market行情/显式因子，研究批准仍待完成。

新任务仍经当前manage、普通硬规模及覆盖检查；在原提交事务内重建计划后，核对冻结actor/scope/source/category/period/fields/标的、missing模式和明确自动确认，再比较当前missing_parts及执行估计credits_max与approved_parts/approved_credits。未知估计unverified拒绝，超限抛安全ScheduleApprovalRequired，confirmed=true不能绕过。通过批准可替代本次普通阈值的人工确认，但不能绕过最终PG调度租约准入或实际HTTP账本。已有同请求保持原幂等产物，不用新策略重写它。

批准信用约束当前无重试规划估计，包含来源适配器已声明的估计范围；不是供应商账单或累计实际支出上限。metadata/重试/并发实际出站仍走统一硬预算；现有估计明确不包含metadata/重试。需区分这两类限制，不从估计上限推出实际调用总数。

## 证据

- `pytest tests/test_schedule_approval.py tests/test_schedule_clock.py tests/test_collection_schedules.py tests/test_independent_collection.py -q --tb=short`：60项通过，新增10项批准测试涵盖恰好上限、零新增、分段/信用超限、未知估计、actor/scope/source/标的/all拒绝及因子字段不得扩大。
- `scripts/verification-research-persistence.py`：85项隔离PG回归通过（14项调度）。相同coverage摘要下Provider估计从正常值变为501、超过草案500，上限在提交重建时拒绝且无job；confirmed=false/true均拒绝。恢复正常估计后，普通阈值调低至1，当前预览需要确认，但有效自动批准可入队。Provider为受控估计，不计真实定时来源下载。
- Ruff检查/格式和Mypy两核心模块通过。没有修改已有manifest、公开DTO或迁移；代码注释解释当前重建检查及规划估计/真实信用的不同语义。

尚未完成超限自动暂停的运行装配、尚未入队范围的持久恢复日志、来源发布时间/日历、研究批准、timer/启停/UI及真实来源自动闭环。ScheduleApprovalRequired提供安全内部原因，但没有由此宣称生产已经会自动暂停。7.x继续未勾选。

## 部署与既有主链回归

collector镜像 sha256:97e9dfd3e460024b972151d6e03099269b8016b5c24a0c6789994bafb2ff0ec7 部署healthy；生产schedule/run表0/0，无新迁移或启用自动任务。`QTRADE_VERIFICATION_ORIGIN=http://127.0.0.1:13000 node tests/verification-independent-collector.mjs` 8项通过，行情384/因子5/两成员池96、桌面手机刷新。`QTRADE_VERIFY_RAW_CAPTURES=true QTRADE_VERIFY_COLLECTION_QUALITY=true scripts/verification-independent-collection.py` 复核4个非空发布原始/标准、来源证据、门禁、分页及消费者摘要通过。这是手工采集真实主链回归，不计自动批准UI或真实定时采集验收。OpenSpec strict validate通过。
