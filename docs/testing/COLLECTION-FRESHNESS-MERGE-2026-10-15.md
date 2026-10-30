# 并发新鲜度刷新提交与证据合并

本轮完成8.1持续刷新所需的原子提交分组；定时生产、供应商最新观察、其他类别策略和告警仍未接通，8.1–8.3继续未完成，整体17/42。

## 规则与边界

新增FreshnessRefresh.commit，仅接受可信生产器准备并验证的候选，不是浏览器提交健康状态的入口。来源请求、归档验证和更新策略计算在事务外进行；事务内按完整scope/key摘要取得PG advisory lock、再次检查当前manage、读取最近结果、合并并追加。所有仓储append也共享相同锁，不能插入刷新读写之间。bound仓储只允许原key，防止跨key锁顺序及越界操作。

合并仅保留collected/published较远的完整周期；同周期取较新的实际观察时刻。来源健康、来源checkpoint和预期规则完全使用本次候选，不能借上次健康隐藏来源故障，也不能将未知规则替换为旧规则。相同规则版本不允许预期周期倒退，让旧数据假装达到“最新”。旧存储快照和引用不改。

过时检查（checked_at小于已存最近检查）返回superseded，不改时钟、不写入、不声称候选已记录；生产器必须重新准备适用的检查后再尝试。同时间重放若合并结果相同返回replayed；有冲突则拒绝。正常新检查返回recorded。superseded不证明所有新发现证据已合入，未来调度器必须处理重试/重新验证，不能直接当刷新成功。

事务连接默认connect_timeout=5秒，advisory等待默认5000ms；适配器可信配置lock_wait_ms严格1–30000。等待超时安全unavailable并回滚，后续提交仍可成功。这里只约束本适配器连接/锁等待，不声称整个供应商链路已满足总deadline。

FreshnessSnapshots.record保留“准确归档观察”语义，历史/受控导入可用；正式持续刷新生产器应使用FreshnessRefresh.commit的合并协议。它没有改成全局强制合并，底层append本身也不定义领域进度规则；不能声称任意绕过核心的写入都具备单调合并。无新增公开写端点、无迁移或冻结schema变化。

## 验证

100项隔离真实PG、领域/认证HTTP及既有元数据回归通过，无跳过：

- 较旧批次新检查保留已提交的采集/发布进度；重放不新增；同时间冲突拒绝。
- 已有较强发布不遮本次unavailable；新source unknown不借旧已验证source；规则未知不继承旧期望；过时检查返回superseded，原记录保持。
- 同规则预期倒退拒绝；8线程竞争保留已有较强checkpoint、最新检查的故障状态正确。
- 事务失败回滚；bound仓储拒绝另一key；只读拒绝，等待锁期间撤权后不写入。
- 100ms受控竞争触发锁等待超时、无部分快照；释放锁后能提交。不是供应商故障证明。

数据库随机research_test_独占创建、全迁移、测试后仅删除自建数据库。Ruff/format、三模块Mypy及OpenSpec严格校验通过。

## 部署与真实数据的受控回放

collector镜像 `sha256:38825717408b6840cc776f8f3a73bf70dcfd86acaca16d4fe01d9a468d71160a` 已部署且ready。CLI verification-freshness-merge.py在既有专用非实盘空间读取真实1股票年度发布，重新验证raw/标准/质量与获取回执，再用原检查epoch+1微秒执行受控历史合并；它没有新供应商请求，不是当前生产探测，不把历史证据重标为刚检查过。

本次first=recorded、second=replayed，行数1→2而非3；较强的原20股票组对应checkpoint仍保留，完整周期2025-12-31、状态unknown。HTTP latest严格结果与保存一致。此证据分类为“真实归档/DB/授权 + 受控历史时刻及回放”，不计实时来源/自动生产成功。

部署后新鲜度UI真实admin/reader、桌面/手机、过期/读取故障/恢复/无记录/刷新及跨域/匿名拒绝回归通过；年度1/5/20原结果、精确代表图表及刷新通过。原020866f界面截图/结果已在私有review目录保留副本，再生成本轮回归证据。UI回归依旧读取已有快照，不是自动刷新UI创建。

命令：隔离PG环境运行test_freshness_refresh_postgres.py及既有freshness/store/routes/collector/research evidence套件（100 passed）；`.venv/bin/python scripts/verification-freshness-merge.py`；apps/web的verification-freshness-ui.mjs及 `QTRADE_BASELINE_PHASE=retest node tests/verification-collection-baseline-recovery-ui.mjs`。

## 后续必做

将真实证据及更新策略接入有界、持久的刷新生产循环，明确superseded的重试与停机恢复；准备检查时刻和策略有效边界，不能只修改旧候选时钟。覆盖来源实际落后、本地未采/门禁未发布、其他类别、当前授权及真实故障。随后完成告警outbox与既有告警过期→去重→恢复链路。所有原Goal要求保留。
