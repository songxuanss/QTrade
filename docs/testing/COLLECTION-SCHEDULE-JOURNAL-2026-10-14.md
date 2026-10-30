# 自动运行请求的不可变恢复日志

推进7.1/7.3，整体仍14/42；timer、来源发布资格和真实自动UI未装配。日志和保留槽不能代替任务/下载/发布成功。

## 接口与事务语义

新增独立v1 ScheduleDispatchDecision及JSON Schema：market内嵌原v1 CollectionJobRequest，research内嵌原v2 ResearchPlanRequest。明确类型、scope/schedule/run/revision/due、policy版本/安全依据及首次冻结时间；原请求范围/模式/身份保存，request_id及correlation_id等于持久run，mode仅missing。冻结草案版本包含原成员快照与批准上限；来源、主体、类别、周期/字段或研究date_axis变化拒绝。无凭证字段；旧schema文件不变，独立导出26份。

0015只追加collector-owned collection_schedule_dispatch，scope/run主键、外键和NULL-safe JSON身份约束，UPDATE/DELETE/TRUNCATE拒绝；0014及已有迁移不改。有效当前授权/lease下冻结，行锁取得之后比较数据库实际时刻，不借旧now延长租约。首次语义写入获胜，重复同意图仅允许候选freeze时间不同，返回首次原时间/摘要；范围、policy或版本改变冲突。重新连接和重新领取读取原决定，不按照新日期计算窗口。

公开核心控制读取先当前read；仓储精确scope/run读取，验证内容摘要与canonical保留槽的schedule/revision/due关联。行情/因子最终准入在原job事务内读取该日志，request必须完全相等；缺失或损坏失败关闭，不留job/part。不是新的供应商下载路径，既有Worker/预算/归档/门禁仍复用。研究日志已类型化，但研究最终准入/timer未装配，不能据此声称研究定时支持。

## 验证证据

- `pytest tests/test_schedule_dispatch.py tests/test_schedule_approval.py tests/test_schedule_clock.py tests/test_collection_schedules.py -q --tb=short`：34项通过，新增8项包含market/research严格往返、完整草案revision、request/correlation/scope/all/凭证拒绝、范围/policy摘要变化及读权限先于仓储。
- `scripts/verification-research-persistence.py`：91项隔离PG回归（20项调度）通过。新增6项：三连接同意图重冻返回首次时间/摘要、改范围冲突、跨scope拒绝、旧lease失效/重领后原日志不变；SQL不可变/缺JSON身份拒绝；更换范围不能入队；缺失、摘要损坏及canonical槽revision不一致拒绝且无job。受控坏证据只写入每例独立临时数据库，不操作生产日志。
- 全部既有定时准入/估计批准和其他研究/用量PG用例继续通过。Provider为受控样本，非真实定时/UI链路。首次新测试文件Ruff排序/长行提示经格式修正，最终Ruff/Mypy四模块通过；未放宽校验。OpenSpec7.x仍不勾选。

来源日历/更新时间、研究准入及批准、超限暂停的timer装配、实际启停/UI/Worker重启自动来源闭环和迟到复核继续未完成。当前草案仍默认disabled，不能通过SQL启用代替验收。

## 部署与手工主链回归

collector镜像 sha256:5dd8738a46fde558e61a4caf38fb6a363bafe60645b4e8ad9e13c52c43f1ecd6 部署healthy，实际末项迁移0015_collection_schedule_dispatch.sql；生产schedule/run/dispatch仍0/0/0，不创建或启动自动计划。现有DB/归档卷保持。

部署后 `QTRADE_VERIFICATION_ORIGIN=http://127.0.0.1:13000 node tests/verification-independent-collector.mjs` 8项通过（行情384、因子5、两成员池96，桌面手机及刷新）。`QTRADE_VERIFY_RAW_CAPTURES=true QTRADE_VERIFY_COLLECTION_QUALITY=true scripts/verification-independent-collection.py` 复核4个非空发布的raw/standard、来源证据、门禁、分页与消费者摘要通过。这是既有手工真实主链回归，不能替代自动计划UI/定时来源验收。OpenSpec strict validate、提交凭证扫描及差异检查通过。
