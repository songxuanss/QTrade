# Design

## Context

QTrade 目前是绿地项目，仓库只有产品、架构与路线图文档，没有业务代码。认证由兄弟项目 `../sso` 提供，QTrade 必须以 OIDC Relying Party 身份接入。Phase 1 的核心风险不是缺少某个功能，而是：数据不可信、回测不可复现、外部引擎耦合过深、实盘路径绕过风控、以及后期无法从单市场扩展到多市场。

本设计基于 `docs/08_LOW_COUPLING_ARCHITECTURE.md` 与 ADR 0001/0002，并使用领域词汇见根目录 `GLOSSARY.md`。

## Goals / Non-Goals

**Goals:**

- 先建立稳定的 Control Plane，再接入可替换的 Compute Plane 与 Execution Plane。
- 保证 SSO 登录、权限、审计、风控、任务编排和 UI 不依赖具体交易引擎。
- 让 Paper Runtime 与 Live Runtime 共享策略版本、订单模型和风控入口。
- 使数据、代码、参数、引擎和环境的每次输入都能通过 Artifact Manifest 复现。
- 支持从一个市场逐步扩展到多市场，而不是为单一 SDK 硬编码平台。
- 在生产默认关闭实盘，并通过 feature flag、审批与演练逐步开启。

**Non-Goals:**

- 不做本地密码、注册、MFA 或 SSO 用户管理。
- 不在 Phase 1 集成 Qlib 或建立完整 AI 研究平面。
- 不实现券商清结算、做市系统、高频托管或多市场全量接入。
- 不把 LEAN/NautilusTrader 的私有对象直接暴露给 UI 或核心领域层。
- 不追求一期引入 Kubernetes、Kafka 或全局微服务拆分。

## Decisions

### D1. 采用模块化单体 + 明确进程边界，而不是初期微服务

**选择：** 一个 FastAPI 应用承载 API 与领域模块，另设 worker、realtime、engine runner、research runner 进程。代码按包和 Port 分界，部署按风险分界。

**替代方案：**

- 全部微服务：过早增加网络、事务、观测和部署成本。
- 单进程处理所有任务：回测和策略沙箱容易拖垮 API，实盘重启风险高。

**边界：**

```text
qtrade-api          认证会话、REST API、任务编排、审计入口
qtrade-worker       采集、回测、报告、告警评估、维护任务
qtrade-realtime     行情连接、指标、告警热路径、实时推送
qtrade-engine-runner 饱和引擎执行，按任务启动或池化
qtrade-web          Next.js UI 与 BFF
```

模块内部采用：

```text
identity-access
market-data
analysis
alerting
strategy
backtesting
execution
risk
audit
operations
```

### D2. Next.js BFF 持有 SSO 交换结果，FastAPI 验证 QTrade 会话

**选择：** Next.js 服务端路由负责 `/auth/login`、`/auth/callback`、`/auth/logout` 和 Back-channel Logout 入口。授权码交换、client secret、access token 与 refresh token 只存在服务端会话存储中；浏览器只持有不可预测的 QTrade Session ID Cookie。FastAPI 通过会话存储读取 actor 与 membership，不信任浏览器自报身份。

**替代方案：**

- SPA 公共客户端直接处理 OIDC：token 暴露面更大，refresh 与 CSRF 更复杂。
- FastAPI 直接承接全部 Web 路由：可行，但会削弱 Next.js BFF 对页面会话和 CSRF 的统一处理。

**关键细节：**

- Pending state 与 nonce 使用服务端临时记录，绑定浏览器会话并设置短 TTL。
- 生产 Cookie 使用 `HttpOnly`、`Secure`、`SameSite=Lax` 或更严格策略。
- Back-channel Logout 验证 JWKS 签名、`iss`、`aud`、`exp`、`jti`、`events`，并用 `jti` 防重放。
- API 的状态变更路由使用与 session 绑定的 CSRF token。

### D3. 领域层只依赖 Port，外部系统只出现在 Adapter

**选择：** API 和领域服务引用 `TradingEnginePort`、`MarketDataPort`、`BrokerPort`、`ResearchPort`、`NotificationPort`、`ObjectStorePort`。生产 Adapter 与测试 Mock Adapter 成对存在，避免没有第二个实现的伪抽象。

**接口原则：**

- Port 返回 QTrade 标准契约，不返回 LEAN、CCXT、券商 SDK 或 Qlib 私有对象。
- Adapter 负责 anti-corruption、重试、限流、错误映射与能力声明。
- Runner 负责隔离执行和资源限制，不让策略代码进入 API 进程。
- UI 只认识 Workspace、Strategy Version、Backtest Run、Order Command 等领域对象。

### D4. 领域状态使用 PostgreSQL，行情存储按数据规模在 TimescaleDB 与 ClickHouse 间决策

**选择：**

- PostgreSQL 保存用户映射、Workspace、Membership、策略、任务、订单、风控、告警、审计与配置。
- 原始供应商响应和标准化 Parquet 保存到 S3/MinIO。
- 若一期以 1m/5m/daily OHLCV 为主，选择 TimescaleDB，降低部署复杂度。
- 若一期要求全市场 tick/order book 或大规模截面扫描，选择 ClickHouse。

**替代方案：**

- 全部 PostgreSQL：容易起步，但大规模分析会受限。
- 一期强制 ClickHouse：查询能力强，但运维与建模成本更高。

**保护边界：** 业务服务只调用 `MarketDataPort`，不得手写具体时序数据库 SQL；存储选择由 adapter 和配置提供。

来源能力与完整性分开建模：按配置在建任务前验证来源、授权、周期和已知归档边界；逐笔时长上限默认不限，仅约束逐笔请求。历史日线与分钟原始批次保留相关交易日历证据，转换版本通过质量评估 Port 生成不可变报告。报告区分已观察交易日、日内完整性和研究前提；不能用成功采集替代证券历史状态、复权、历史股票池或财务 PIT。参考 ADR 0006。

### D5. 按目标市场选择可替换的事件引擎

**已决策：** Phase 1 以 A 股逐笔数据为目标，采用 NautilusTrader adapter，见 [ADR 0004](../../../docs/adr/0004-nautilustrader-for-tick-first-engine.md)。LEAN runner 原型未达到本地端到端验收条件，官方固定镜像在约 30 分钟内未能拉取完成；镜像体积也不适合一期快速验收。此为接入/运维适配性取舍，不代表 LEAN 撮合语义测试失败。

两种引擎都只能通过 `TradingEnginePort` 接入，私有类型不能渗入领域、API 或 UI。Nautilus 也需要 QTrade 自己适配 A 股交易日历、合约规格和执行规则，不视为现成的中国市场插件。

**接入方式：**

```text
BacktestSpec
  -> freeze inputs and manifest
  -> prepare read-only dataset
  -> launch engine runner
  -> collect private result files
  -> normalize to BacktestResult
  -> store immutable artifacts
```

**失败映射：** 引擎崩溃、超时、OOM、非法策略、数据缺口分别映射为标准任务错误，不暴露堆栈给未授权用户。

### D6. Paper 与 Live 共用 Order Intent -> RiskEngine -> Broker Port 链路

历史下载的整体范围上限与执行分段分离：整体仍默认不限，新建任务按七天连续半开区间拆分，子任务使用稳定身份和公共分组标识，事务内创建以避免部分入队。每段独立 checkpoint/重试/审计并显示范围；已有完整下载保留不可变，不原地修改历史任务。市场终端按已下载周期查询，日线下载不意味着拥有分钟数据。

**选择：** 策略输出 Order Intent，不直接调用 broker。RiskEngine 依据规则生成 allow、deny 或 require_approval；只有 allow 及通过审批的路径生成 Order Command。Paper Broker Adapter 与 Live Broker Adapter 实现同一契约。

**订单安全属性：**

- 每个 Order Command 有唯一幂等键。
- 状态机显式记录 submitted、partial、filled、cancelled、rejected、timeout、failed。
- 断线后先进入安全状态，再恢复并对账。
- Live 受全局 feature flag、账户授权、审批和 Kill Switch 共同控制。

### D7. 回测与研究产物使用 append-only 对象存储与 manifest

**选择：** 每次任务创建前冻结输入，生成 Artifact Manifest；结果、日志、图表数据和报告作为不可变对象保存。数据库保存索引和查询摘要，不把大结果作为可编辑行。

**原因：** 历史报告不能因数据修正或代码升级而改变；同时避免大 JSON 拖慢领域库。

### D8. 实时路径采用 Valkey/Redis Streams，重计算进入 worker

**选择：** realtime 进程负责供应商连接、心跳、基础校验和事件分发；指标、告警、报告、参数扫描进入 worker。WebSocket 只推送用户需要的摘要与状态。

**替代方案：**

- 直接在 API 中处理行情：容易造成请求延迟和级联失败。
- 一期使用 Kafka：运维成本高，当前吞吐收益不明确。

### D9. 安全默认值

- 策略沙箱默认拒绝网络、文件系统、进程和主机访问，只有显式授权的资源可用。
- Broker 凭证使用信封加密，按 Workspace 与账户授权访问，界面只显示掩码。
- 审计记录不保存 secret 明文。
- 生产启动检查 HTTPS issuer、session secret、数据库迁移和实盘开关。
- CI 失败阻止发布，镜像扫描失败阻止生产部署。

### D10. 按业务上下文组织模块与数据所有权

**选择：** 将身份与访问、市场数据、策略、回测、执行、风控、告警和审计作为独立领域上下文。
契约、领域、应用、适配器、运行基础和进程组装保持单向依赖；worker/realtime 不导入 API。
每个上下文拥有聚合及其数据，跨上下文通过公开接口和版本化集成事件协作，禁止直接修改内部实体。
同步裁决保留授权、风控和交易不变量；异步副作用采用事务 Outbox、至少一次交付与幂等消费。
不强制全系统事件溯源。依赖测试同时覆盖 SDK 泄漏、层反向依赖与进程互相导入。

**原因：** 相比按进程复制业务或提前微服务化，此方案把修改集中在规则所有者，保留未来抽取服务的路径。
具体聚合、所有权、接口前置条件及重构步骤见 `docs/architecture/MODULES.md` 与 ADR 0003。

## Risks / Trade-offs

- [目标市场未确定] → 先实现市场无关契约、Instrument provider 与 mock/paper 通道；Phase 0 完成数据源、引擎和 broker PoC 后再锁定范围。
- [LEAN 集成成本被低估] → 用独立 runner 和标准结果转换器隔离；若 PoC 无法满足数据或执行语义，切换 NautilusTrader，不改 UI 与领域服务。
- [TimescaleDB/ClickHouse 选择摇摆] → 业务只依赖 `MarketDataPort`，通过 adapter 和基准测试切换。
- [一期范围过大] → 按身份/数据/回测/paper/风控/实盘里程碑交付；Live 可以延期，但风控与审计规格不降级。
- [实盘误下单] → 默认关闭、dry-run、幂等键、预交易检查、审批、Kill Switch、对账差异阻断和演练共同兜底。
- [策略沙箱逃逸] → 独立容器、最小权限、资源限制、网络白名单、只读数据挂载和审计。
- [审计与产物存储膨胀] → 对象存储保存大产物，数据库只存索引；配置生命周期与归档策略，但审计删除必须受控并独立记录。
- [供应商限流或数据质量差] → 幂等补采、退避、Data Quality Issue、数据延迟熔断和依赖数据的新订单阻断。

## Migration Plan

绿地项目没有数据迁移，但发布仍按阶段推进：

1. **Foundation Alpha：** 仓库、CI、配置、数据库迁移、SSO BFF、Workspace/RBAC、审计基础。
2. **Data Alpha：** Instrument、交易日历、mock 数据源、标准 OHLCV 查询、数据质量与 Parquet 批次。
3. **Research Alpha：** Strategy Version、静态校验、MockEngineAdapter、LEAN runner PoC、标准回测报告。
4. **Runtime Alpha：** Paper Runtime、Order Intent、RiskEngine、Paper Broker、订单状态机和对账模拟。
5. **Production Candidate：** 真实数据源、告警、观测、备份恢复、受控实盘 feature flag 与演练。

回滚策略：

- 应用按镜像摘要回滚。
- 数据库迁移必须提供向前修复或兼容回滚说明。
- 禁止在未通过实盘安全检查时通过回滚绕过 feature flag。
- 已完成 Backtest Run 与 Artifact Manifest 不受回滚修改。

## Open Questions

- 第一期目标市场与数据供应商：影响 Instrument provider、交易日历、数据授权和成本。
- 首个真实 Broker/交易所通道：影响 adapter 开发顺序和实盘 GA 是否延期。
- LEAN 与 NautilusTrader PoC 结果：多资产默认 LEAN，crypto/tick 场景需重新决策。
- TimescaleDB 与 ClickHouse 数据基准：以一期数据粒度、标的数量和查询目标确定。
- A 股或其他受限市场的程序化交易合规边界：需在真实资金接入前确认。

市场图表的默认周期来自当前工作区该标的已发布数据集，目录已有标的也须补充周期信息。仅有日线时默认日线及一个月窗口；切换标的重新初始化图表，用户仍可主动选择其它周期，空周期保持明确提示。
