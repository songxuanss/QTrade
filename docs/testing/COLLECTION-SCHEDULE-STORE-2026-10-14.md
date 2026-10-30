# 独立自动计划持久基础

整体14/42，7.1–7.5未完成。本阶段只有严格输入、授权内部控制、PG持久化及租约/槽身份；未接来源/日历策略、实际timer/自动提交、启用HTTP或UI，不声明自动采集可用。

现有QTrade calendar_schedule是日历修订，未复用或改动。新contracts/core/PG schedules模块支持冻结行情/所选因子及类型化研究选择、成员快照引用、时区/分钟精度本地时间、日/周/季操作意图、回看/有界补跑参数及批准部分/信用；明确bool true、无凭证字段。意图不证明供应商频率；默认disabled。当前ScheduleControls在创建/读/停用前核对当前manage/read，未提供用户启用；来源权益、日历/发布时间和执行授权仍须接入。

仅追加0014，scope/schedule主键及JSON身份NULL-safe约束，paused原因严格一致；SKIP LOCKED短领取，token/version/expiry fencing同时检查数据库当前时间，repeat enable保持原owner，disable撤销。UTC规范slot和完整revision派生run UUID，唯一键及UPDATE/DELETE/TRUNCATE禁止确保重启/过期重领不变。reservation不是业务任务或下载结果，后续必须与公开采集请求幂等身份、授权/预算/确认衔接，不能据此扩大完成范围。

## 证据

- `pytest tests/test_collection_schedules.py -o addopts='' -q --tb=short`：9通过，覆盖明确自动确认、严格布尔/规模/补跑界限/时区/分钟精度、冻结revision变化、拒绝token/licence/password、授权拒绝先于存储。
- `scripts/verification-research-persistence.py`：75项隔离PG回归通过（含4个schedule案例、每例新隔离DB）。持久重开、默认disabled不领取、幂等/冲突、跨scope、4独立连接只有一owner、重复enable不撤销、expiry/旧token/disable拒绝、推进下一时刻、SQL NULL与空draft拒绝、3连接同slot只一run、恢复原run、SQL不可变约束均通过。均为受控DB证据，非timer/来源/UI计划验收。
- Ruff/Mypy三模块通过。首次Ruff长行/新增timezone案例缩进格式失败后修正，无功能测试放宽；迁移在隔离库测试完善后部署，已部署SQL不再改写。
- collector镜像 `sha256:c2b2016c08fb0cde397e3c76e1e644fe1ee4acbc3ef1a976f4fc90080de30a33`，实际迁移末项0014_collection_schedule.sql；部署schedule/run表均0行，未创建或启动生产计划。原DB/对象卷保持；Web/API无变更。

稳定部署现有下载UI8项通过（行情384、因子5、两成员池96、桌面手机及刷新）；4个非空发布raw/standard/source capture/quality与消费者摘要再复核通过。它们证明既有主链未回归，不是自动计划UI或自动来源验收。SQL异常经安全unavailable关闭，不向调用者转发连接详情。schema独立导出，不修改旧发布。全部余项仍按原Goal推进，尤其Source CalendarPort/发布时间/稀疏类别、有界补跑、撤权暂停、实际自动UI及修订复核不能由此阶段代替。

## 时间计算与触发前授权复核

独立 schedule_clock 按草案 IANA 时区计算下一时刻和有界补跑槽，返回 UTC。创建时首槽严格晚于创建时刻；恢复包含当前游标，未来游标不回退。周频用配置星期，季频用季度首日，只表示操作节奏，不证明来源此时发布数据。补跑按 catchup_days 个连续24小时限定扫描，夏令时最多增加一个本地日期；多年停机不会逐日遍历。窗口前只记录省略界限，不编造遗漏运行计数。不存在的本地时间跳过，重复时间仅取首次。交易日历、来源发布时间和稀疏类别资格仍待接入。

reserve_authorized_run 复核冻结执行主体当前 manage，不能用启用者权限替代。明确撤权先以有效租约暂停并清所有权，再拒绝保留运行；版本/token/调用时间/数据库时间均防护，旧所有者不能暂停新状态。授权 unavailable 拒绝保留，但不误写为撤权，恢复须重新查权。

- pytest tests/test_schedule_clock.py tests/test_collection_schedules.py -q --tb=short：16项通过，覆盖准确边界、时区等价、多年停机、周季跨年、夏令时、无时区拒绝、创建统一计算及授权暂不可用不暂停/保留。
- scripts/verification-research-persistence.py：76项隔离PG回归通过；新增领取后撤权，验证持久暂停、零运行、旧租约拒绝及恢复授权后重新保留。5项调度PG用例各自使用临时数据库，不计生产自动运行。
- Ruff检查/格式及Mypy三模块通过。无新增迁移，不改0014校验和。collector部署镜像 sha256:f44b39f21414d47e66433fafbc5d07657dec8fe88b0311b056ad6ec68688a1e8 healthy，生产计划/运行仍0/0；7.1–7.5未完成，整体14/42。

本轮部署后 `QTRADE_VERIFICATION_ORIGIN=http://127.0.0.1:13000 node tests/verification-independent-collector.mjs` 再次8项通过（行情384、因子5、两成员池96，桌面手机/刷新）。`QTRADE_VERIFY_RAW_CAPTURES=true QTRADE_VERIFY_COLLECTION_QUALITY=true scripts/verification-independent-collection.py` 复核4个非空发布的原始/标准/HTTP响应证据、质量、分页和消费者摘要通过。它们仅证明既有手工采集主链未回归；不替代自动计划UI/定时来源验收。OpenSpec strict validate通过。
