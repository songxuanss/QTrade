# QTrade

QTrade 是一个面向专业交易者与量化研究员的交易分析、告警、策略研究、回测与实盘执行平台。项目正按 OpenSpec 分阶段交付；当前已实现工作台、身份与 Workspace 权限、基础行情与数据管理、A 股历史逐笔采集等能力，策略回测和交易执行仍在开发中。

## 核心定位

本地部署、登录方式与可复现的功能验收场景见 [用户验收指南](docs/testing/USER-VERIFICATION.md)。

- **分析平台**：行情、指标、组合、风险、归因、数据质量一站式分析。
- **策略平台**：策略代码、参数、版本、实验、回测、对比与复现。
- **执行平台**：纸面交易、受控实盘、订单生命周期、对账、风控与审计。
- **告警平台**：行情信号、策略信号、风险阈值、数据源与系统运行状态统一告警。
- **安全边界**：统一接入 `../sso` 的 OIDC / OAuth 2.1 能力，QTrade 自身不重复造登录模块。

## 仓库骨架与快速检查

| 路径 | 职责 / 模块入口 |
| --- | --- |
| `apps/web/src/app/` | Next.js Web / BFF；包入口为 `src/index.ts` |
| `apps/api/src/qtrade_api/main.py` | FastAPI API 组装入口 |
| `apps/worker/src/qtrade_worker/main.py` | 后台任务进程与采集消费者 |
| `apps/realtime/src/qtrade_realtime/main.py` | 实时行情与推送进程入口 |
| `apps/common/src/qtrade_composition/` | API 与 Worker 共用的依赖组装根 |
| `packages/contracts/src/qtrade_contracts/__init__.py` | 共享标准契约 |
| `packages/domain/src/qtrade_domain/__init__.py` | 领域行为与 Port |
| `adapters/mairui/src/qtrade_mairui/` | MaiRui A 股历史逐笔 Adapter |
| `adapters/notifications/src/qtrade_notifications/` | Email / Webhook / 站内通知 Adapter |
| `adapters/mock/src/qtrade_mock/__init__.py` | Mock Adapter 入口；外部实现统一放在 `adapters/*` |

本地开发依赖与测试命令：

```bash
scripts/check-python.sh
cd apps/web && npm run check && npm run test:e2e && npm run test:auth
```

认证使用 `../sso`，领域与 API 只依赖 Port；骨架不包含密码登录、外部 SDK
或实盘执行。依赖安装和服务启动命令将在对应任务完成后补充。

## 项目宪法

[CONSTITUTION.md](CONSTITUTION.md) 固化不可协商的硬约束：规格先行、Ports & Adapters 边界、策略代码隔离执行、SSO 唯一身份、凭据管理、产物不可变、真实执行、未完成能力披露、实盘受控、时区口径与工具链锁定。OpenSpec change 只能在其范围内细化，不能违反；冲突时以宪法为准。

```bash
uv run --locked python scripts/check_constitution.py
```

CI 通过 `tests/test_constitution.py` 在 `pytest` 阶段执行该检查；`pull request` 上的 `--spec-diff` 门禁与接入方式见 [docs/testing/CONSTITUTION-GATE.md](docs/testing/CONSTITUTION-GATE.md)。纯重构需在提交信息标注 `[no-spec-change]`。

## OpenSpec 需求管理

当前需求以 OpenSpec 为唯一变更入口：

```bash
openspec list
openspec status --change add-qtrade-phase1-mvp
openspec validate add-qtrade-phase1-mvp --strict
```

首个进行中的变更为 `openspec/changes/add-qtrade-phase1-mvp/`，包含 proposal、能力规格、设计与任务清单。后续新增或修改行为时，先创建/更新 OpenSpec change，再进入实现。下列文档是背景资料与架构参考；若与当前 OpenSpec delta 冲突，以 OpenSpec 为准。

| 文档 | 内容 |
| --- | --- |
| [CONSTITUTION.md](CONSTITUTION.md) | 不可协商的硬约束与对应的自动化/评审强制方式 |
| [docs/01_PRODUCT_SCOPE.md](docs/01_PRODUCT_SCOPE.md) | 用户、场景、一期范围、必须补充的能力与一期不做事项 |
| [docs/02_ARCHITECTURE.md](docs/02_ARCHITECTURE.md) | 推荐技术架构、开源项目选型、模块边界与数据流 |
| [docs/03_ROADMAP.md](docs/03_ROADMAP.md) | Phase 0 到 Phase 5 的分阶段路线图与退出标准 |
| [docs/04_UI_UX_BLUEPRINT.md](docs/04_UI_UX_BLUEPRINT.md) | 专业交易台 UI 的信息架构、页面、交互与视觉基线 |
| [docs/05_SSO_INTEGRATION.md](docs/05_SSO_INTEGRATION.md) | 基于 `../sso` 的 OIDC / PKCE / introspection / logout 集成方案 |
| [docs/06_SECURITY_RISK_COMPLIANCE.md](docs/06_SECURITY_RISK_COMPLIANCE.md) | 密钥、权限、交易风控、审计、合规与运维要求 |
| [docs/07_PHASE1_BACKLOG.md](docs/07_PHASE1_BACKLOG.md) | 一期 Epic / Story / 验收标准与优先级 |
| [docs/08_LOW_COUPLING_ARCHITECTURE.md](docs/08_LOW_COUPLING_ARCHITECTURE.md) | Ports & Adapters、模块边界、数据契约与扩展方式 |
| [docs/adr/0001-use-ports-and-adapters.md](docs/adr/0001-use-ports-and-adapters.md) | 低耦合架构决策记录 |
| [docs/adr/0002-qlib-as-optional-research-adapter.md](docs/adr/0002-qlib-as-optional-research-adapter.md) | Qlib 集成决策记录 |

## 建议的第一期原则

1. **先可信、再丰富**：数据质量、复现性、审计和风控优先于功能数量。
2. **实盘必须受控**：一期只接一个目标市场 / 券商 / 交易所，默认 dry-run，实盘必须经过风控、审批与 kill switch。
3. **开源底座可替换**：通过 `TradingEngineAdapter` 抽象隔离 LEAN / NautilusTrader / 自研引擎。
4. **专业 UI 不等于堆页面**：优先做到高信息密度、低延迟、键盘友好、可保存工作区与清晰的异常呈现。
5. **一期交付可用闭环**：登录 -> 数据 -> 分析 -> 告警 -> 策略 -> 回测 -> 纸面/受控实盘 -> 复盘。

## Python 工程准备

Python 配置固定使用 Python 3.12；`uv.lock` 锁定服务和适配器依赖。

```bash
uv python install 3.12
uv lock
uv sync --locked
uv run ruff check .
uv run mypy
uv run pytest
```

Phase 0 待定选择见 [决策模板与问题对照](docs/adr/templates/README.md)。

本地已有 Python 3.12 时，可在有 PyPI 访问权限的终端运行：

```bash
bash scripts/bootstrap-python.sh
```

脚本检查 Python 版本和 PyPI 连通性，将 uv 安装到忽略提交的 `.tools/uv`，
生成锁文件并同步 `.venv`，然后执行 Ruff、mypy 与 pytest。
已有锁文件时使用 `--locked`；初始空测试集的退出码 5 会明确报告，实际测试失败仍退出非零。
当前已创建 Python 3.12.14 的 `.venv`，uv 已安装到 `.tools/uv`，依赖已锁定。
脚本默认使用 `.tools/uv-cache`，避免受限环境无法写入全局缓存目录。
需要下载新依赖时仍须具备包仓库访问权限。

## 前端工程准备

要求 Node.js 22.12+ 与 npm。前端依赖通过 `apps/web/package-lock.json` 锁定。

```bash
cd apps/web
npm ci
npm run check
npm run build
npm run test:e2e
npm run dev
```

开发页面为 http://localhost:3000，健康检查为 `/health`。
`check` 包含 ESLint、Prettier、TypeScript 与 Vitest。
当前包含单元、SSO、工作台、键盘命令面板和浏览器可访问性测试；失败会退出非零。
添加浏览器测试后，先运行 `npx playwright install --with-deps chromium`。
Playwright 自动在 3100 端口启动 Web，避免与常规开发端口冲突。

当前 Next.js React lint 插件尚不兼容 ESLint 10，因此锁定 ESLint 9.39.5；升级时需验证插件兼容性。

## 本地容器环境

```bash
docker compose up --build --wait
```

Web 为 http://localhost:13000，API 健康检查为 http://localhost:18000/health，
依赖就绪检查为 http://localhost:18000/ready。
默认凭据仅用于本地；数据库、缓存和对象存储不发布主机端口，实盘关闭。
Worker 会在配置数据源许可后轮询 PostgreSQL 中的可运行采集任务；Realtime 目前提供进程与依赖健康检查。
PostgreSQL 保存领域状态；独立 TimescaleDB `market-db` 保存不可变行情数据。
启动时 `migrate` 服务执行两套 checksum-locked 迁移，成功后才启动业务进程。
TimescaleDB 使用固定镜像 digest 与独立数据卷；`MARKET_DATABASE_URL` 仅供服务端使用。

MinIO 使用 `infra/minio/Dockerfile` 从固定 upstream commit 构建，替代不可访问的发行镜像。
本机 Docker bridge DNS 无法解析 GitHub，因此本地构建使用 host 网络；运行时仍使用隔离的 Compose 网络。
验收脚本：`.venv/bin/python scripts/selftest-compose.py`，覆盖健康状态、非 root 进程及缓存/行情数据库故障恢复。
停止环境：`docker compose down`（保留数据卷）。

### A 股历史逐笔下载

数据管理页面允许管理员按标的和 UTC 起止时间提交下载任务。单次范围默认不限，超过七天自动拆分为多个七天以内的子任务；可用 `QTRADE_TICK_DOWNLOAD_MAX_DAYS` 设置逐笔单次上限，`0` 表示不限。当前 A 股来源为 MaiRui，服务端需设置有效的 `MAIRUI_LICENCE`（复制 `.env.example` 为 `.env` 后填写），然后执行 `docker compose up -d --build`。许可只传给 API 和 Worker，不填写在浏览器表单。首次启用时，本地组合会创建 MinIO 的 `qtrade` 桶。

MaiRui 历史逐笔档案从 2026-08-13 起累积；请求更早的区间会明确失败。保存的是逐笔原始批次，页面按批次显示下载进度。交易日以 `Asia/Shanghai` 时间过滤。港股不在当前版本范围内。

### 告警投递

告警规则可以指定 `in_app`、`email` 和 `webhook` 收件目标。邮件需配置 `SMTP_HOST` 与 `SMTP_SENDER`，认证凭据可选；Webhook 目标 ID 映射到 `QTRADE_WEBHOOK_DESTINATIONS` JSON，主机名还必须列入 `QTRADE_WEBHOOK_ALLOWED_HOSTS`。生产环境只允许 HTTPS。投递任务和每次尝试保存在 PostgreSQL；临时失败会退避重试，响应正文与认证凭据不会写入投递摘要。配置示例见 `.env.example`。

模块设计与 DDD 上下文、数据所有权、依赖方向及重构路径见 [模块设计](docs/architecture/MODULES.md)。

### 股票池成员管理与整池下载

数据管理提供行业、概念、指数成分搜索，以及自定义股票池。选择分类后先解析并
保存不可变成员快照。卡片「下载全部成员」定位到下载配置；选择日期与周期、
预览后点击「一键下载所有成员」提交。可多选已保存股票池或全选下方列表，
重合股票只下载一次；默认仅补缺，也可明确选择全部重下。每股自动七天拆分，
显示逐股/逐段进度和失败重试，刷新保留运行。成员按页查看，保留来源观察日期、
名称差异和当前成员不能代表历史成分的说明。来源代码冲突的条目禁用。

参考成员模块由独立 contracts/core/MaiRui/PostgreSQL 包组成，使用单独迁移和
`qtrade_data` schema。`DATA_COLLECTION_DATABASE_URL` 可指定独立数据库；
本地 Compose 默认与业务库共用实例。所有权及迁移边界见
[采集数据所有权](docs/data-collection-ownership.md)。聚合计划与运行使用独立模块；
原始采集/发布由 compatibility Adapter 接入既有 Worker，完整独立 Worker、远程
服务和股票池市场视图仍在实施。请求节奏由
`QTRADE_COLLECTION_MIN_INTERVAL_SECONDS` 配置（默认3秒），不限制下载日期范围。
来源限流时等待并退避；失败可仅重试失败任务。「停止未展开任务」不会中断已创建的任务。
