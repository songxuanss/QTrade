# Foundation 验收与 CI

## 已验证

- `docker compose up --build --wait --wait-timeout 240`：八个运行服务健康，迁移服务成功退出。
- `.venv/bin/python scripts/selftest-compose.py`：Web/API、后台进程、依赖探测、非 root 身份，
  停止缓存或行情数据库后 readiness 返回 503 而 liveness 保持 200，重启依赖恢复 ready。
- `bash scripts/check-python.sh`：Ruff、严格 mypy、依赖方向检查及真实 pytest 测试。
- `npm run check`、`npm run build`、`npm run test:e2e`：质量检查、真实单元测试与 Chromium 页面/
  404/axe 可访问性检查。空测试不再允许通过。
- `tests/test_ci_gates.py` 执行工作流中的实际 required gate，对失败、取消及跳过状态要求非零。

`.github/workflows/ci.yml` 在 push/PR/manual 触发 Python、前端、安全、镜像构建及基础设施集成。
镜像步骤依赖 Python/前端成功，所有失败保留为失败，没有 `continue-on-error`；生产镜像不发布。
当前仓库没有 GitHub remote，尚未触发远程 Actions；本地执行等价检查和 gate 测试。

## 安全扫描发现（保持阻断）

- Bandit 发现对象存储探测 URL 未限制协议，已改用 HTTP 客户端、显式 HTTP(S) 限制及禁止
  重定向；修复后扫描无 medium/high 发现。
- pip-audit 发现 pytest 8.4.2 的 PYSEC-2026-1845，已升级到修复版本 9.0.3 以上并重新运行测试。
- npm 完整依赖扫描发现开发工具链间接引入的 braces 漏洞
  [GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm)。
  官方通告尚无修复版本；不通过降级 Next.js 或忽略通告让 CI 变绿。生产依赖另行扫描。
- API 镜像 Trivy 扫描发现 Debian util-linux 系列的 HIGH 通告，未发现 CRITICAL；
  需进一步更新/最小化基础镜像并确认修复可用性。镜像扫描未通过，不代表生产就绪。

扫描数据随时间变化，应每次发布重新执行。以上只是 Foundation 证据，不替代 SSO、真实行情、
策略隔离、Paper/Live 安全、备份恢复等后续验收。完整目标尚未完成。

## 专业 UI 基线（5.1）

深色工作台使用共享布局、导航、颜色/间距/字体 token 与独立 workbench 模块。
未连接身份时显示空数据和权限占位，不编造行情或资金。桌面和 390px 手机截图已人工审阅。
五个 Chromium 测试通过，覆盖三张视觉快照、键盘跳转、移动导航/Escape、权限页、
空态/错误/加载状态与 axe（零违规）；生成基线后再次运行比较通过。
后续业务页面必须继续使用这些组件并增加操作流程、错误/权限和响应式 UI 测试。

## Port 与不可变 Manifest（2.2 / 2.4）

六个 Protocol 的 Mock 交互覆盖提交、查询、取消、通知去重、账户快照、行情分页、
对象校验、错误标准化和 Workspace 作用域。Manifest 由代码/数据/参数等冻结输入生成，
规范化 JSON 与 UTC 时间，稳定内容确定 ID；对象存储的条件创建拒绝覆盖同一 run。
16 个 Manifest 用例验证参数顺序、时间等价、版本/种子变更、新数据不改变历史和非法输入。
这些验证不替代实际引擎计算、授权检查或真实 Broker 接入。

## S3/MinIO Adapter（2.6）

174 个 Python 用例通过。S3 Stubber 验证条件上传参数、同内容重试、SDK 标准错误与安全消息、
下载校验、非法 key/endpoint。`scripts/selftest-objectstore.py` 对当前运行的 MinIO 实测通过：
读写/查询/缺失/校验失败、不可覆盖、Workspace 隔离、特权写入损坏检测，八个并发不同内容
创建同一 key 只有一个成功，历史内容保留。相同基础契约也在 Mock 上执行。
CI 在 Compose 健康后执行该集成验证；前端快照环境显式安装 Noto CJK 字体。

## 身份数据库基础（3.1）

185 个无外部依赖测试通过；八个 PostgreSQL 集成测试仅在显式目标下运行，
通过 `scripts/selftest-postgres.py` 对 Compose PostgreSQL 实际执行，全部通过。
覆盖空库/重复/四个并发迁移、失败初始迁移整体回滚、失败升级保留原数据、校验和锁定、
拒绝旧程序迁移更高版本数据库、唯一身份/个人工作区/成员、五个角色、会话过期/作用域/
密文元数据约束、审计内容及 UPDATE/DELETE/TRUNCATE 拒绝。CI 单独执行该集成阶段。
向前修复与兼容应用回退说明见 `adapters/postgres/README.md`。SSO/BFF/授权链路仍未完成。

## 首次登录映射（3.4）

190 个无外部依赖测试通过，12 个实际 PostgreSQL 集成测试通过。
新增四个身份映射场景覆盖首次/重复初始化、六个并发登录仅一个用户/工作区/成员、
相同 subject 不同 issuer 独立身份、禁用用户拒绝及成员创建失败全事务回滚。
重复登录保留工作区名称/归档状态、角色/禁用成员，已删除成员不会自动重新授权。
入口仅接受已通过 OIDC 验证的身份；OIDC 验签和 BFF 路由另在 3.3 验收，尚未接通。

## 生产 UI 与整栈复验

已重建 Compose，七服务健康；在实际 API 镜像运行身份迁移两次，首次应用 0001、第二次
报告 already current。缓存停止/恢复与对象存储并发验证再次通过；pip-audit 无已知漏洞，
Bandit 无 medium/high 发现（npm/镜像未修复项仍阻断）。

生产浏览器检查发现 Next.js 流式 missing 页面返回 200，已增加渲染前 route guard 返回 404，
并提供与工作台一致的缺失页。短屏桌面导航改为内部滚动，访客身份区保持可见。
`npm run check`/build 通过，四个前端单元测试、六个 E2E/四张视觉快照比较通过；
`npm run test:production` 对 localhost:13000 实测桌面/手机 axe 零违规、无页面运行异常、
开发 preview 和未知 section 真正返回 404。CI 增加该生产浏览器验收。

## 安全会话（3.2）

213 个 Python 测试通过；14 个实际 PostgreSQL 集成测试通过；七个 Chromium 测试通过。
新增 Session 模块覆盖 opaque cookie、AES-256-GCM 密文、nonce/会话绑定/篡改/密钥轮换、
精确过期边界、单会话与用户全会话撤销、真实数据库重建后恢复、即时失效授权、生产
Cookie 标志和不安全配置拒绝。浏览器 fixture 验证 HttpOnly 不可由 JavaScript 读取，
access/refresh/ID token 不出现在响应、Cookie、local/sessionStorage，伪造 logout 无效。
完整 OIDC 和 SSO 本地联调仍未完成；该任务的配置与边界见 `docs/security/SESSIONS.md`。
Bandit 无 medium/high 发现；新增 cryptography 后 pip-audit 无已知漏洞。

## OIDC BFF（3.3）

`bash scripts/check-python.sh`：222 passed，14 个 PostgreSQL 集成用例默认跳过；
`scripts/selftest-postgres.py` 对实际 Compose PostgreSQL：14 passed。
`apps/web` 的 `npm run check`：22 单元测试通过，lint/format/typecheck 通过；build 通过。
`npm run test:auth` 对生产 standalone Next.js：三个 Chromium OIDC/退出/隐私/axe 场景通过；
`npm run test:e2e`：七个浏览器用例与四张原有视觉快照比较通过，无更新基线。
重建 Compose 后 `npm run test:production`：桌面/手机 axe、真实 404、预览隔离与无页面异常通过。
Bandit 无发现，pip-audit 无已知漏洞。OAuth 单元用例真实签发 JWT，覆盖 state/nonce/
issuer/audience/签名/过期；使用独立计数确保失败发生在 token 校验阶段。
生产测试发现并修复回调 Cookie 覆盖和登录 Link prefetch 重建事务；登录链接使用原生导航。
真实 sibling SSO 联调、back-channel logout 与业务对象 RBAC/CSRF 仍分别属于 3.9/3.5/3.6/3.7。
配置与模块信任边界见 `docs/security/OIDC.md`。

## SSO Back-channel Logout（3.5）

`bash scripts/check-python.sh`：234 passed，17 个实际 PG 用例默认跳过，41 source mypy/架构检查通过。
`scripts/selftest-postgres.py`：17 passed，新增 issuer/sub/sid 作用域、持久重放、八消费者竞争、
receipt/会话更新失败整体回滚与安全重试。迁移 0002 在 Compose 实库升级成功，重复执行 current。
`npm run check`：34 单元测试通过；build 通过；生产 standalone `npm run test:auth` 四个浏览器场景通过。
新增过期/错误 audience/错误签名/events/nonce/iat/jti 负例，以及两标签全局退出/新登录后重放拒绝。
七个普通 Chromium 用例/四张既有视觉快照仍通过，未更新快照；Compose 七服务健康、非 root、
cache 故障恢复通过。协议、部署与 subject 全局撤销边界见 `docs/security/BACKCHANNEL-LOGOUT.md`。

## Workspace RBAC（3.6）

`bash scripts/check-python.sh`：251 passed，20 个 PG 用例默认跳过；48 source mypy/架构检查通过。
`scripts/selftest-postgres.py`：20 passed，新增真实 PG/HTTP 的目标 Workspace 角色裁决、成员
变更即时生效、跨工作区不可见、viewer 写拒绝、admin 管理、最后 admin 并发撤销保护。
五个内置角色使用明确的非层级 grants；admin 包含新个人工作区所需的研究/执行操作，live
审批仍独立授予 risk_manager，不能替代业务审批与 RiskEngine。对象归属不一致返回 404。
浏览器写守卫绑定会话 CSRF，普通 form 与其他会话的 CSRF 不能修改成员；新增 API 漏守卫
会使架构检查失败。实际策略/回测调用方仍须在 7.1/7.6 验证对象状态与拒绝审计。
`npm run check`：34 单元测试通过；build 通过；`npm run test:auth` 四个生产浏览器场景通过，
新增桌面/手机登录态视觉基线逐张审阅，随后普通比较通过。七个原有浏览器用例及四张原有
视觉基线比较通过；Compose 生产 `npm run test:production` 桌面/手机 axe、404/preview 隔离通过。
前端只显示服务端验证的 workspace 名称/角色；Bandit 无发现。模块边界见 AUTHORIZATION.md。
审计任务 3.8 与策略/告警/账户全链 CSRF 任务 3.7 尚未完成，不将成员写测试充作这些验收。

## Audit request context（3.8，进行中）

`bash scripts/check-python.sh`：268 passed；23 个数据库用例由独立实库阶段全部通过。
52 source mypy、Ruff 与架构检查通过。审计详情使用受限类型，仅记录对象 ID、会话摘要、
权限裁决与成员角色/激活状态前后值；不接受任意 payload 或 token 字段。
登录、退出、全局撤销和成员变更在业务事务内写审计；审计写失败时业务变更回滚。
HTTP 边界注入服务端生成的 trace UUID 与物理连接 IP，不信任 X-Forwarded-For/X-Request-ID。
BFF 调用记录的是 BFF 连接地址；尚未建立可信代理链，因此不将其宣称为最终浏览器 IP。
生产 standalone 身份浏览器四例、普通浏览器七例及六张视觉快照比较仍通过。
失败认证与其余拒绝路径尚需补齐，3.8 保持未勾选。

## Identity audit completion（3.8）

`bash scripts/check-python.sh`：270 passed；实际 PostgreSQL 独立阶段 24 passed。
新增 CSRF 拒绝审计、匿名 OIDC 失败审计以及审计不可用时的安全错误响应；实际数据库
验证审计插入失败会回滚全局退出会话更新及 replay receipt，允许修复后安全重试。
前端质量检查与 34 单元测试通过；生产 build、四个身份浏览器场景与七个普通浏览器
场景通过，六张视觉基线无需更新。错误签名与缺失 state 均新增失败审计且不创建身份。
边界与后续完整业务事件/导出范围见 `docs/security/AUDIT.md`。

## Adapter capabilities（2.3，进行中）

能力声明与注册表增加市场、粒度、订单类型、交易时段、实盘开关和健康状态。
注册绑定声明与实际 Adapter；重复 ID 拒绝，选择时检查能力和当前健康状态。
未知/降级/不可用状态不允许选择，恢复健康后无需重注册即可恢复。
Workspace 范围只读 API 复用实时 RBAC，越权不可发现适配器；不支持能力返回 422，
健康故障返回 503。生产注册表默认空，不宣称已配置外部 Broker 或引擎。
`bash scripts/check-python.sh`：291 passed；55 source mypy、Ruff、架构检查通过。
新增用例覆盖注册唯一性、声明不可变、各能力维度拒绝、恢复和 HTTP 权限。
设置页能力视图与后续实际 Adapter 接入仍需完成，2.3 保持未勾选。

工作区设置页现通过服务端会话读取能力 API，显示支持范围、健康状态及不可用实盘能力。
空注册表与读取失败分别显示明确空态/错误态，不展示模拟外部连接。
前端质量检查、34 单元测试、生产 build 与五个身份浏览器用例通过。
新增桌面/手机能力页视觉基线逐张审阅；手机无横向溢出，两个视口 axe 无违规。
能力注册与后续业务选择接入仍在进行，2.3 暂不勾选。

能力注册现在检查声明种类与实际 Port 一致；六个 Mock Port 绑定已验证，选中的
MarketDataPort 完成标准 Bar 写入/查询。所有声明均不含实盘能力，job-only engine/
research 保持 unknown，不被误宣称为可运行模拟器。294 Python tests、56 source
mypy/架构检查通过；五个生产浏览器场景含两个新能力视觉基线的普通比较通过。
2.3 的能力声明、注册、API 拒绝与 UI 不可用展示验收完成；完整交易/行情调用方
继续在对应功能任务接入该选择接口。说明见 `docs/architecture/CAPABILITIES.md`。

## Instrument Master（4.1，进行中）

新增领域 Port、应用权限裁决与 Mock Instrument 存储。规格冲突保留既有规格并生成
pending 数据质量问题；同一冲突重试复用 issue，不覆盖历史规格。批次先完整校验，
重复标识、跨 Workspace 与精度不一致不会产生部分写入。Decimal 精度检查不经过
浮点或默认 Decimal context，超长数值回归用例验证不会被舍入后误判。
查询覆盖标识、名称、交易所、市场、启用状态与稳定分页。应用拒绝 viewer 导入、
跨 Workspace 查询及归档资源访问；读取 Adapter 返回值再次验证 Workspace。
299 Python tests、59 source mypy、Ruff 与架构检查通过。PostgreSQL 实现与 HTTP
导入/查询仍需完成，4.1 保持未勾选。

Instrument Master PostgreSQL 增量迁移 0003 与存储 Adapter 已实现。既有规格不可
UPDATE/DELETE/TRUNCATE；冲突保留 proposed 内容且重试复用 issue。八并发相同导入
只有一次创建；八并发冲突只生成一个问题。数据库写失败整批回滚，写事务内重新
检查成员角色。标准查询覆盖启用/禁用、Workspace、市场/交易所、Unicode casefold
名称筛选与稳定分页；历史 payload 损坏映射安全 unavailable 错误。
299 Python tests、60 source mypy/架构检查通过；独立实际 PG 阶段 26 passed，包含
空库/并发迁移、checksum、后续升级失败回滚与以上存储场景。尚未将 0003 应用到
常规 Compose 数据库；将在 API 接线后升级并验证运行环境。HTTP 层仍待完成。

Instrument Master HTTP 已接线：导入、过滤查询、包含禁用状态的详情及冲突详情 API。
HTTP 用例覆盖真实 PG 会话/存储、即时角色变化、跨 Workspace 不可见、viewer 写入
拒绝、CSRF、保留旧规格、重试以及安全校验错误；slash-bearing ID 详情可查询。
301 Python tests、61 source mypy/架构检查通过；独立实际 PG 阶段 27 passed。
API/web 镜像已重建，Compose 常规数据库升级 0003 成功，重复执行 already current。
七服务健康、非 root、依赖故障恢复与生产桌面/手机 axe、404/preview 隔离通过。
4.1 的模型、导入/查询 API 与验收完成；模块说明见 INSTRUMENT-MASTER.md。

## Trading calendar core（4.2，进行中）

新增不可变交易日历、分段/隔夜交易时段、显式闭市/提前收市覆盖、Workspace 快照
Port 与市场状态应用服务。时段开端包含、结束排除，隔夜交易保留起始 trading date。
UTC 与本地转换检测不存在/歧义时间；不存在边界拒绝，歧义边界必须显式 fold。
未知日历或覆盖范围外不猜测开市。所有测试日历为显式合成计划，不宣称真实交易所
节假日数据；目标市场/供应商仍未选定。版本不可覆盖，查询可冻结历史版本。
308 Python tests、64 source mypy/架构检查通过；新增常规/周末/显式假日、午间休市、
提前收市、Tokyo 隔夜、New York/London 不同 DST 日期、春季不存在时间、秋季 fold、
重叠时段、权限、Workspace 隔离、版本和查询范围测试。持久化与业务 HTTP 接入仍
需完成，4.2 保持未勾选。

CalendarSchedule v1 已导出独立 JSON Schema 与示例，契约/领域转换保留日期覆盖、
本地时钟与 DST fold。新增 round-trip、schema 一致性与非法时区、重复/越界 weekday、
带偏移时钟、反向日期范围、范围外覆盖测试。320 Python tests、65 source mypy/架构
检查通过；4.2 的持久化与 HTTP 接入仍待完成。

Calendar PostgreSQL Adapter 与增量迁移 0004 已实现。八并发同版本导入仅保存一份；
相同版本不同内容拒绝，最新版本与冻结历史版本可独立查询，跨 Workspace 不可见。
写事务锁定 Workspace 并重新检查成员角色，viewer 无法写入；数据库触发器拒绝
UPDATE/DELETE/TRUNCATE。320 Python tests、66 source mypy/架构检查通过；实际 PG
28 tests 通过，包含迁移并发、checksum 与 0005 失败升级回滚。0004 尚未应用常规
Compose 数据库，待 HTTP 接线后统一升级验证。4.2 仍进行中。

Calendar HTTP 接线已加入常规 API：POST /calendars/import 接受 CalendarSchedule v1；
GET /calendars/sessions 按 [start,end) 交易日期返回 UTC 时段；GET /calendars/state
使用显式 aware at 参数，可选 version 冻结历史计划。统一 Workspace guard/CSRF，
导入大小限制、安全校验错误、跨 Workspace payload 拒绝。HTTP 回归覆盖匿名、viewer、
CSRF、显式闭市、UTC 开市边界、版本冲突/历史查询、naive 时间及范围外 unavailable。
321 Python tests、67 source mypy/架构检查通过。实际 PG HTTP 联合验证、常规运行升级
与 Instrument 时区一致性仍待完成，4.2 保持未勾选。

Instrument market-state 查询已接入 CalendarService：从 Workspace-scoped Instrument
解析 calendar_id，强制 Instrument/Calendar IANA timezone 一致，禁用 Instrument 拒绝。
API GET /instruments/market-state 使用 instrument_id、aware at 与可选 calendar version。
回归验证缺失日历、当前版本时区不一致、冻结历史版本及禁用品种。322 Python tests、
67 source mypy/架构检查通过。4.2 运行环境升级与实际 PG HTTP 联合验证仍待完成。


Calendar 4.2 final verification (2026-10-04): 0004 applied to the standard Compose
PostgreSQL database; API image rebuilt and API reports healthy. Mock HTTP acceptance
covers authenticated import, session and state queries, workspace and role enforcement,
calendar version pinning, and instrument calendar resolution/timezone consistency.
Real PostgreSQL integration reports 28 passed, including concurrent persistence,
revision immutability, role recheck, and migration checks. Full Python suite reports
322 passed, 28 skipped; strict mypy and architecture checks pass. Synthetic schedules
are used because target exchange/provider/holiday data has not been selected. Requirement
4.2 is complete for the explicit versioned calendar service and test fixtures; supplying
authoritative exchange schedules remains a separate provider/data onboarding decision.

Phase 0 market-store decision 4.3 is recorded in ADR 0002. It explicitly treats 1m/5m/daily
bars and a 2,000-instrument, five-year envelope (~983M minute rows/market) as planning
assumptions because provider and market remain undecided. A pinned-image, isolated-container
5M-row comparison supports the initial TimescaleDB bar-store choice for interactive per-symbol
ranges and shared PostgreSQL operations; ClickHouse won bulk load, compression, and the broad
scan. The probe is directional, not billion-row capacity evidence. `MarketDataPort` remains the
only business-facing store seam; strict architecture checks and the full suite pass. ClickHouse
remains a documented replacement option if the selected workload shifts toward tick data or
large analytical scans.

Task 4.4 shared dataset boundary (in progress): MarketDataService enforces READ/DATA_MANAGE
and validates adapter page scope, range, granularity, ordering, uniqueness, page limit, and
cursor shape before exposing results. Domain validation rejects duplicate/scoped-mismatched
bars, canonicalizes dataset order including same-time different granularities, and supplies
stable JSON/hash representations across Decimal scale and equivalent UTC offsets. Mock and
future persistence adapters share these rules. Invalid query types map to safe PortError.
335 Python tests, strict mypy over 69 source files, Ruff and architecture checks pass. The
selected TimescaleDB persistence Adapter, actual storage integration, and HTTP API are still
required before marking 4.4 complete.

Task 4.4 TimescaleDB persistence (in progress): separate checksum-locked market-store
migration and TimescaleMarketData Adapter now persist immutable dataset hashes plus versioned
Bar payloads in an indexed hypertable. Keyset cursors are bound to workspace/dataset/instrument,
granularity and range; queries preserve UTC ordering and [start,end) boundaries. Concurrent
identical snapshot retries create one dataset; mismatched content conflicts. Database triggers
reject changes, truncation and later appends. A creation-transaction insert constraint enforces
parent existence while avoiding the first-chunk FK/insert lock cycle found by concurrency tests.
Real pinned TimescaleDB tests: 4 passed, covering 8 concurrent retries, concurrent new snapshots
across chunks, semantic Decimal/timezone retry, failed-COPY atomic rollback, scope, pagination,
invalid cursor and immutability. Full suite: 335 passed, 32 integration-only skipped; 71 source
mypy, Ruff and architecture checks pass. HTTP integration, adapter performance measurement
and the normal Compose market database remain outstanding; 4.4 is not checked complete.

Task 4.4 HTTP/runtime integration (in progress): authenticated POST/GET dataset Bar endpoints
now enforce DATA_MANAGE/READ, CSRF, scoped immutable imports, safe validation errors and
bounded UTC OHLCV responses. HTTP tests cover viewer writes, absent CSRF, duplicate/mismatched
payloads, stable retries/conflicts, pagination, exclusive end boundary, empty snapshots and
no-data ranges, cross-workspace reads, missing datasets and invalid timestamps/cursors.
337 Python tests pass (32 optional database tests skipped), strict mypy over 72 files and
architecture checks pass. Compose now has 8 healthy runtime services plus a successful one-shot
migration service. Separate market-db uses the pinned TimescaleDB digest and its own volume;
readiness checks the extension and market schema. Core migrations report already current and
market migration 0001 applied. Cache and market-database failure/recovery drills pass, followed
by production desktop/mobile UI/axe, hard 404 and preview-isolation checks. CI includes actual
Timescale integration tests. Combined authenticated HTTP/real-store acceptance and adapter
performance measurements are still needed before 4.4 is marked complete.

Task 4.4 final acceptance: six actual TimescaleDB tests pass. The combined HTTP case uses real
PostgreSQL identity mapping, encrypted persisted sessions, current membership guards and
TimescaleDB Bar storage; it verifies CSRF, stable retries, pagination/cursor scope, cross-workspace
404 and immediate admin-to-viewer write denial. The adapter sample stores 100k bars across
100 instruments; ten 1000-bar queries measure 57.5 ms median and 162.3 ms p95, including
connection and contract validation, below the 1s local regression budget. Raw measurements
are tracked in market-data-adapter-2026-10-04.json and limitations are explicit. Full Python
suite: 337 passed, 34 optional integration tests skipped; 72 source mypy and architecture
checks pass. Runtime/production UI validation from the preceding entry remains current.
4.4 is complete; raw Parquet/batch replay remain task 4.5.

Task 4.5 raw-record/Parquet foundation (in progress): RawRecord preserves immutable original
bytes, MIME metadata and contiguous collection position. PyArrow 25.0.1 is locked and confined
to a dedicated Adapter; the versioned Parquet envelope uses binary payloads to retain exact
precision text/CSV line endings/malformed semantic records for later interpretation. Independent
Parquet inspection and codec contract tests verify exact round trips, empty files, deterministic
same-release output and rejection of corrupt/version/schema/count/sequence mismatches. Mock
codec is explicitly a test format. 352 Python tests pass, 34 optional DB cases skip; strict
mypy over 76 sources, Ruff and architecture checks pass. Batch publication, immutable metadata,
read-time hashes and replay/new dataset persistence remain outstanding; 4.5 is unchecked.

Task 4.5 raw batch publication workflow (in progress): BatchInput/ArchivedBatch and an immutable
metadata Port now support RawBatchService publish/read. Publication verifies codec round-trip,
create-only object identity and readable bytes before metadata commit. Exact retries read the
original stored codec bytes; changed content/source metadata conflicts. Metadata failure leaves
an unreferenced object that a retry safely reuses. Reads check repository scope, SHA, count and
ordering. Tests cover concurrency, roles/workspace, upgraded writers, failure/recovery, corrupt
object/scope and invisible objects. UTC collection times distinguish DST folds while equivalent
offsets retry cleanly. 359 Python tests pass, 34 optional DB cases skip; 79 source mypy, Ruff
and architecture checks pass. PostgreSQL metadata, actual object/DB integration and replay remain
outstanding; 4.5 is still unchecked.

Task 4.5 PostgreSQL/MinIO acceptance (in progress): migration 0005 adds immutable scoped raw
batch metadata and publisher identity. Transaction-local membership checks reject writes after
an admin becomes a viewer, including requests with previously captured access. Actual PostgreSQL
tests cover concurrent retries, changed metadata, archive/scope denial and SQL mutation guards.
The combined real Parquet/MinIO/PostgreSQL case confirms eight publishers produce one object
and one metadata row, original precision/line-ending bytes survive reconstructed readers,
changed records conflict and corrupted objects fail checksums. All 31 PostgreSQL integration
tests pass. Full Python: 359 passed, 37 optional integration cases skipped; Ruff, strict mypy
over 80 sources and architecture checks pass. Dataset replay remains outstanding; 4.5 is unchecked.
The locked Python images rebuild successfully, migration 0005 applies to the local runtime,
all eight services are healthy and API readiness passes database/market/cache/object checks.

Task 4.5 replay application (in progress): immutable replay request fingerprints and parent
lineage distinguish new dataset snapshots from existing versions. Versioned transformation
Ports consume verified archives; the internal standard-Bar JSON implementation has explicit
format/provenance validation. Reports enumerate only the checks actually performed. Ten focused
tests cover revised rules preserving original raw bytes/Bars/metadata/backtest references,
concurrent exact retries, request conflicts, writer removal, publication-failure recovery,
role/scope/lineage checks, invalid transformed outputs and corrupt reports. Persistent dataset
metadata and actual market-store replay acceptance are still needed before 4.5 is complete.
Full Python checks pass: 369 tests, 37 optional integration cases skipped, strict mypy over
84 sources, Ruff and architecture checks.

Task 4.5 final persistence acceptance: migration 0006 and PostgresDatasetRepository publish
immutable receipts plus ordered batch links atomically. Foreign keys, parent/version checks,
creation-transaction link guards and deferred completeness reject missing, partial or late
provenance. All 33 actual PostgreSQL tests pass, including live role changes and SQL mutation
guards. All seven actual Timescale tests pass: new transformation rules create fresh versions
without changing old raw bytes/Bars/metadata/backtest references, failed metadata publication
recovers the same market snapshot, and a later viewer role prevents metadata publication.
The replay test uses actual PG/PyArrow/Timescale with mock objects; the separate raw-publication
case uses actual PG/PyArrow/MinIO. Full Python: 369 passed, 40 optional integration cases skipped;
strict mypy over 85 sources, Ruff and architecture checks pass. Collector and data-workbench
integration remain separate tasks; 4.5 acceptance behavior is implemented.
Additional raw-format rejection and UTC/DST-fold range tests bring the full suite to 375 passed,
40 optional integration cases skipped; types/lint/architecture checks pass. Migration 0006 applies
to the rebuilt runtime, all eight services are healthy, cache and market-database failure/recovery
drills pass, and production desktop/mobile UI/axe, hard 404 and preview-isolation checks pass.

Task 4.6 historical ingestion: provider-neutral domain and application worker support bounded
pages, shared workspace/source/license rate limits, retry-after, capped exponential retries,
durable staged-page recovery, deterministic page IDs, idempotent immutable archive writes,
ordered replay and cursor-cycle detection. PostgreSQL migration 0007 persists lease fencing,
pending raw bytes, retry state, checkpoints and one-way cursor history. Thirteen worker tests cover
vendor throttling/network failures, reruns, process reconstruction, cursor cycles, role changes,
lease expiry and concurrent invocations; the PostgreSQL integration suite has 35 passes and the
Timescale suite has 8 passes, including archive-to-dataset replay. Full Python checks: 388 passed,
43 optional integration cases skipped; Ruff, strict mypy over 89 sources and architecture checks
pass. The local Compose stack applies migration 0007, all eight services are healthy, and the
dependency failure/recovery drill passes. Web check passes 34 tests; production desktop/mobile
accessibility, hard 404 and preview-isolation smoke passes. External provider credentials and
worker deployment composition remain configuration work; no vendor was chosen for this task.

Task 4.7 data quality: versioned calendar grids detect missing ranges and session deviations;
source-order checks detect duplicates and out-of-order bars; configured price-jump and trailing
median volume thresholds create stable, evidence-bearing issues. Suspicious price/volume/order
observations can be marked suspect and duplicate/calendar deviations quarantined. PostgreSQL
migration 0008 persists scoped, queryable issues and permits only open→claimed→closed handling;
repeated scans are idempotent, evidence mutation/deletion is rejected, and live role changes
fence writes. Four quality-service tests cover all six rule families, deterministic results,
calendar deviation, query/claim/close, and authorization. Actual PostgreSQL integration has 36
passing tests. Full Python: 394 passed, 44 optional integration cases skipped; Ruff, strict mypy
over 95 sources and architecture checks pass.

Task 4.8 data lineage: `DataLineageService.for_bar` follows an exact normalized Bar through its
immutable dataset and transformation/report hash, verified raw batch and license reference,
completed collection task, and relevant quality findings. It checks each identity and workspace
boundary and returns no raw vendor payload. Unit tests cover a complete chain, viewer access,
missing datasets, and invalid timestamps. The real Timescale acceptance flow now completes a
PostgreSQL-backed ingestion task, replays Parquet pages into the market store, and resolves the
stored quality issue and source task from a Bar. All 8 Timescale integration tests pass; the full
Python suite is 394 passed / 44 optional integration tests skipped, with lint/types/architecture
checks green.

Task 4.9 realtime market supervision: the provider port normalizes heartbeat/tick pulses while a
policy-driven service tracks heartbeat freshness, event lag, sequence/timestamp disorder,
disconnects, retry deadlines and recovery. PostgreSQL migration 0009 persists workspace-scoped
health snapshots, and the scripted mock provider exercises fresh and stale heartbeats, delay,
out-of-order events, disconnection, backoff, and reconnect recovery. Three behavior tests cover
the state transitions and permissions; 37 actual PostgreSQL integration tests pass, including
health persistence and live role revocation. Full Python is 397 passed, 45 optional integration
cases skipped; Ruff, strict mypy over 99 sources and architecture checks pass. Tick history and
browser push remain subsequent consumer tasks. The API also exposes authenticated, workspace-
scoped health list/detail routes; HTTP tests cover anonymous rejection, viewer reads, and other-
workspace isolation. The rebuilt eight-service stack is healthy and production desktop/mobile
accessibility, 404, and preview-isolation checks pass.
