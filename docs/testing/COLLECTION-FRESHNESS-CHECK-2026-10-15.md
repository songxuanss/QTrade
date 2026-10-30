# 日线主动核验与获取时点证明

本阶段推进 OpenSpec 8.1/8.3，仍不完成所有类别、自动监控及告警要求；42 项中保持 17 项完成。核验面向已有 CN 未复权日线发布，不隐式创建下载任务。

## 设计与时间语义

新增 FreshnessCheck 核心，通过端口取得真实交易日历与预期发布时间，并验证不可变归档、所选原始行及质量决定。管理权限在来源调用前和原子提交时检查；预期跨发布时间或提交已被较新结果超越时，至多完整重准备两次。不会给旧证据换一个新时钟。日历调用使用原共享来源缓存、pacing、usage_scope 和预算。

完整日线不能只看最大记录日期。原始获取回执必须绑定精确所选 raw 行的内容摘要，且为真实 history 获取。缓存重读即使发生在收盘后，也不能证明原始数据何时取得。最新预期日使用完整 available_at，保留次日发布等日期边界；历史日保守要求次日取得，不能外推今天规则的时分覆盖所有历史。没有符合条件的周期明确 unverified；有较早完整周期则保留较早进度。

只有近 300 秒真实取得且覆盖当前预期周期的已验证证据才可作为来源观察。刚下载旧历史仍是来源未知；重新核验不下载最新行情，也不能从旧历史推断来源滞后或本地采集滞后。完整周期、历史覆盖、数值准确性及研究就绪分别判断。

## 公开边界与 UI

共享严格 FreshnessCheckRequest 只有 publication_id；新增 JSON Schema，旧 schema/manifest 不改写。collector POST `/v1/freshness/check` 验证受信服务身份及当前 manage，返回严格 FreshnessResult/no-store，不提供任意快照写接口。QTrade POST `/data/collector/publications/{id}/freshness/check` 要求管理权限及 CSRF，从 scoped 清单推导完整 key 并校验返回身份。READ 的 GET 快照接口继续没有来源调用或写入。

已完成日线结果保留“查看/刷新新鲜度快照”，管理角色另见“重新核验新鲜度”。说明元数据额度及不会下载最新行情，错误覆盖旧健康显示，旧证据保留供参考；只读角色无管理按钮。关键设计/时间/幂等/失败边界在实现和公开契约中有注释。

## 受控验证

- `tests/test_freshness_check.py`：旧历史不冒充最新、近期预期周期可证明来源、跨发布时间重新准备、READ 无调用/写入、来源失败不记成功、superseded 有界重试。
- `tests/test_daily_freshness_evidence.py`：同日无发布时间拒绝、发布时间后接受、次日发布不能把前日盘中获取变完整、较早完整日保留、相同行数错误内容拒绝、cached_history 拒绝、嵌套数据精确行证明及未知 response_rows 不伪造数量。
- 隔离 PostgreSQL 套件 101 项通过：真实文件校验、HTTP 服务身份/角色/额外字段拒绝、事务合并与查询持久一致；其日历和行情为受控证据，不计真实来源就绪。
- 核心/桥接/质量/既有采集分组 89 项通过；后续简化测试夹具后，直接核验/桥接/证据 39 项再次通过。
- Ruff/Mypy、Web TypeScript/ESLint/Prettier及生产镜像构建通过。

## 真实部署验收

实际 13000 → 3122 → 18100 → 独立 PostgreSQL/对象归档及既有真实年度下载记录验证通过。无新增付费授权或实盘操作；本轮核验复用之前 UI→Worker→供应商获得的归档，不宣称本轮新下载了行情。

`cd apps/web && node tests/verification-freshness-ui.mjs`：实际 SSO 管理员1440桌面与只读390手机，键盘、axe0、无横向溢出；管理员命令完成后 checked_at 更新，重新加载和只读读取仍一致。601288 的预期截至2026-10-14、发布截至2025-12-31，状态继续unknown/source_unknown，未借旧历史伪造来源健康。READ 无按钮且带有效 CSRF 直接 POST403；管理员缺CSRF403、跨空间403/404、匿名401。浏览器受控clock模拟过期、读/核验server action传输中断后保留旧记录并待核验，恢复后成功；该故障不等于供应商实际宕机。最终扩充命令故障和跨空间路径后整脚本再次通过。

`.venv/bin/python scripts/verification-daily-freshness-evidence.py`：三份真实年度601288发布各243行的raw/standard/质量重放与新完整周期证明通过；复核UI保存快照及公开查询一致，snapshot_written=false，没有偷偷替换/追加测试快照或探测最新行情。

`cd apps/web && QTRADE_BASELINE_PHASE=retest node tests/verification-collection-baseline-recovery-ui.mjs`：原1/5/20年度任务、代表601288精确冻结图表及刷新再次通过。保持每股243条，不把图表30日窗口当全年展示。截图在忽略的私有review目录，已观察桌面按钮/时间字段及手机单列/只读状态；不含凭证。此脚本名称中的restart沿用前阶段，本轮未重启消费者数据库或重新执行下载，不冒称执行中恢复。

Web镜像 `sha256:823b12c240e91030b8915ad09a87deb732a86caa79a2c0fc4cc7baa7a6d339b0`；上述UI时collector镜像 `sha256:6e2859e06ebaea4a6397884e427791a5d0a43a993be54c976c6bc695aa3657d2`。最终仅补公开方法注释和成功日志publication关联ID字段，完成后再次构建/健康核对；最终collector镜像为 `sha256:12ed6cb2a7d7ded34f4fb1ebfc08e90eba64f05e4ba2bb745110fbc0043e35e7`，健康通过且完整管理员/只读桌面手机脚本再次通过。API3122保留原配置安全重启。

## 未完成范围

持久自动刷新及租约/撤权/重启、独立最新来源观测、来源真实不可用与恢复、因子和稀疏研究规则、运维健康查询、告警 outbox 和项目告警去重/恢复仍需实现验证。受控拒绝路径及管理员手动核验不能代替这些验收，也不能完成全部主业务覆盖矩阵。
