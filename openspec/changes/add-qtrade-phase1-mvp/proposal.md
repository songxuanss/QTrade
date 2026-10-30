# Proposal: 建设 QTrade 专业量化交易一期 MVP（add-qtrade-phase1-mvp）

## Why

专业交易者和量化研究员目前缺少一个同时覆盖行情分析、告警、策略研究、可信回测、纸面交易与受控实盘的统一平台。QTrade 第一期需要交付一条可信、低耦合、可长期扩展的专业交易工作流，而不是一次性堆砌交易功能。

## What Changes

- 新建 QTrade 绿地项目，采用模块化单体与 Ports & Adapters：
  - Web/BFF、API、worker、realtime、engine runner、research runner 部署边界先行分离。
  - 领域与 API 代码不得直接依赖 LEAN、Qlib、CCXT、券商 SDK、ClickHouse/TimescaleDB 等外部实现。
  - 通过 `TradingEnginePort`、`MarketDataPort`、`BrokerPort`、`ResearchPort` 等接口隔离外部系统。
- 接入 `../sso`：
  - 使用 OIDC Authorization Code + PKCE 与 BFF 服务端会话。
  - QTrade 不保存密码、不实现注册/找回密码/MFA。
  - 建立 SSO 用户映射、Workspace、Membership 与对象级 RBAC。
- 建立市场数据能力：
  - Instrument master、交易日历、历史与实时行情、幂等采集、数据质量、血缘和不可变原始数据。
- 建立专业分析工作台：
  - Market Watch、K 线、基础指标、布局保存、实时更新、数据源健康。
- 建立统一告警：
  - 覆盖行情/策略、风险、数据、系统四类事件，支持规则、去重、静默、通知与处置。
- 建立策略生命周期：
  - Strategy 与 Strategy Version 分离，提供参数 schema、静态校验、沙箱资源限制和状态机。
- 建立可信回测：
  - 事件驱动引擎适配、成本/滑点/延迟模型、标准化报告、实验对比、不可变 Artifact Manifest。
- 建立纸面交易与受控实盘：
  - Paper Runtime 完整交付；Live Runtime 仅单市场/单账户小范围试点，默认 feature flag 关闭。
  - Broker Adapter、订单生命周期、幂等下单、断线恢复、对账、人工审批与暂停能力。
- 建立风控与审计：
  - 所有 Order Intent 必须经过 RiskEngine 变成 Order Command 后才可提交。
  - 提供限额、预交易检查、熔断、Kill Switch、凭证加密和不可抵赖审计。
- 建立运维基础：
  - Docker Compose、迁移、结构化日志、metrics、trace、健康检查、备份恢复和 CI/CD。

### 非目标与安全边界

- 不实现本地密码登录或用户凭证存储。
- 不做多市场、多券商全量接入；一期只选择一个目标市场和一个受控实盘通道。
- 不做高频托管、做市系统、完整券商清结算、期权定价平台或无代码策略生成器。
- 不在 Phase 1 集成 Qlib；未来仅通过 `QlibResearchAdapter` 作为研究适配器接入。
- 策略不得直接访问 Broker Adapter、生产数据库、密钥或互联网。
- 实盘发布必须经过回测、Paper Runtime 验证、风控配置、审批和回滚方案。

## Capabilities

### New Capabilities

- `platform-architecture`: 模块边界、Ports & Adapters、标准契约、不可变产物与可替换引擎基础。
- `identity-access`: SSO OIDC 集成、QTrade 用户映射、Workspace/Membership、对象级 RBAC 与会话失效。
- `market-data`: Instrument、交易日历、历史/实时数据采集、质量检测、血缘和可回放数据集。
- `analysis-workbench`: 专业工作台、行情监控、K 线、指标、实时状态与工作区布局。
- `alerting`: 多来源告警规则、事件生命周期、去重静默、通知渠道与处置记录。
- `strategy-lifecycle`: Strategy/Strategy Version、参数、校验、沙箱、状态机与发布治理。
- `backtesting`: 回测任务、成本与现实约束建模、标准化指标报告、复现与对比。
- `execution`: Paper/Live Runtime、Broker Adapter、订单生命周期、幂等提交、恢复与对账。
- `risk-control`: 预交易检查、限额、熔断、Kill Switch 与实盘审批。
- `audit-compliance`: 安全、策略、交易、配置与风控事件的不可抵赖审计和查询导出。
- `operations`: 部署、迁移、配置、观测、备份恢复、任务治理和发布安全。

### Modified Capabilities

无。当前项目尚无主规格。

## Impact

- 新建前端、后端、worker、runner 与基础设施代码；无既有业务代码需要兼容迁移。
- 新增 `../sso` OIDC client 配置与 Back-channel Logout 集成。
- 引入 PostgreSQL、TimescaleDB 或 ClickHouse、对象存储、Valkey/Redis、任务队列和 Docker Compose。
- 引擎候选默认为 QuantConnect LEAN，并通过独立 runner 与适配器调用；NautilusTrader 保留为 crypto/tick-first 备选。
- 所有实盘订单路径、策略发布路径、密钥访问和审计查询都受权限与风控约束。

## 关键待确认假设

- 第一期目标市场、数据供应商授权、首个 Paper/Live Broker 或交易所尚未最终确定。
- LEAN 与 NautilusTrader 需在 Phase 0 PoC 后定版；默认在多资产场景选择 LEAN。
- TimescaleDB 与 ClickHouse 需根据一期数据粒度和数据量定版；业务代码只能依赖 `MarketDataPort`。
- 实盘功能是否进入一期 GA 取决于目标市场接口权限、监管边界和风控演练结果；即使延期，也不降低风控与审计规格。
