# QTrade 项目宪法

- 版本：1.0.0
- 最后修订：2026-11-02
- 适用范围：整个仓库（`apps/`、`packages/`、`adapters/`、`tests/`、`scripts/`、`docs/`、`openspec/`）
- 地位：本文件是不可协商的硬约束集合。`openspec/changes/**` 只能在其范围内细化，不能违反。
- 冲突优先级：`CONSTITUTION.md` > `openspec/changes/**` > `docs/**`；后两者冲突时以 OpenSpec 为准。

## 如何执行

1. 每一条都带 `**强制**` 行，标明是 `automated` 还是 `review`。
2. `automated` 条文由 `scripts/check_constitution.py` 与 CI 强制执行；条文引用的每个路径必须真实存在，否则检查失败。
3. `review` 条文不设自动门禁，必须在提交说明、变更归档或验收文档中给出可复核的证据。
4. 新增或修改条文属于行为变更：先走 `openspec/changes/`，再改本文件，并提升版本号（不兼容收紧 = MAJOR，新增条文 = MINOR，措辞澄清 = PATCH）。
5. 不得为了让 CI 变绿而删除条文或把 `automated` 降级为 `review`；确需放宽时走修订程序并在变更中说明理由。
6. 门禁实现、复跑命令与当前边界见 `docs/testing/CONSTITUTION-GATE.md`。

## A1 规格先行：OpenSpec 是唯一变更入口

任何改变外部可观察行为的提交，必须在同一变更中包含 `openspec/changes/<change>/` 的 delta 更新，且实现不得超出已批准的 delta。纯重构、注释与格式修复不要求 delta，但提交信息必须带 `[no-spec-change]` 标记。OpenSpec 产物一律使用中文。

**强制**：automated — `openspec/config.yaml`、`scripts/check_constitution.py`

## A2 端口与适配器边界不可穿透

领域层只依赖 Port 与契约包；不允许多个领域上下文相互越过公共 facade，不允许业务层直接依赖数据库、消息队列或 Web 框架。新增外部集成必须先定义 Port，再在 `adapters/` 实现。

**强制**：automated — `scripts/check_architecture.py`、`tests/test_architecture.py`

## A3 外部引擎与厂商 SDK 只允许出现在 adapter

LEAN、NautilusTrader、Qlib、CCXT、ClickHouse、TimescaleDB、券商 SDK 及行情供应商 SDK 不得出现在 `apps/`、`packages/` 的 import 图中，包括字符串形式的动态 import。引擎以独立进程或容器运行，输出必须转换为 QTrade 标准结果。

**强制**：automated — `scripts/check_architecture.py`

## A4 用户策略代码只在隔离 Runner 内执行

任何对用户提交源码的 `exec` / `eval` / `compile` 只允许出现在 `apps/runner/` 与 `adapters/runner/`。API、Worker、领域与应用层不得解释或执行策略源码，只能冻结输入并入队。Runner 必须保留 CPU、内存、文件、进程与输出上限。

**强制**：automated — `scripts/check_constitution.py`、`apps/runner/src/qtrade_runner/sandbox.py`、`adapters/runner/src/qtrade_runner_adapter/strategy_program.py`

## A5 身份只来自 SSO

QTrade 不实现密码注册、密码登录、密码找回或本地用户凭据存储。认证走 `../sso` 的 OIDC Authorization Code + PKCE；禁止引入密码哈希或口令校验库。对象级授权必须在后端执行，前端隐藏按钮不构成权限控制。

**强制**：automated — `scripts/check_constitution.py`、`tests/test_auth_bridge.py`

## A6 凭据不入库、不入日志、不入产物

密钥、Token、会话 Cookie、登录凭据不得出现在受版本控制的文件、提交信息、测试日志、截图、报告或导出的 CSV/HTML 中。本地凭据只存于被忽略的 `.env*` 与 `.tools/`。新增密钥字段必须有掩码与审计。

**强制**：automated — `scripts/check_constitution.py`、`.gitignore`

## A7 回测与研究产物不可变且带 manifest

每次回测/研究产物必须绑定代码版本、数据版本、参数、引擎版本、适配器版本、环境镜像摘要与 seed，并写入不可变 manifest。报告读取时必须校验一致性；哈希不一致即视为失败，不得降级为警告继续展示。

**强制**：automated — `tests/test_artifacts.py`、`packages/domain/src/qtrade_domain/artifacts.py`

## A8 量化结论必须来自真实执行

订单、成交、现金与持仓必须由真实模型回测、真实 Runner 与持久化队列产出，禁止用 fixture、预置结果或伪造收益替代。验收必须覆盖：任务创建 → Worker 消费 → 数据落库 → 报告可见，并在刷新后复核最终业务结果。

**强制**：review — `scripts/verification-strategy-portfolio.py`、`docs/testing/BACKTEST-COVERAGE.md`

## A9 未完成能力与外部故障必须显式披露

不得把未接通的能力标记为已完成，不得用 fixture 的通过冒充真实集成就绪。被外部依赖阻塞（交易日历缺失、供应商不可用、L2 数据未接入）时，必须在验收文档与 UI 中写明影响范围与回退方式。

**强制**：review — `AGENTS.md`、`docs/backtesting/QUANT-MODEL-READINESS.md`

## A10 实盘默认关闭且受控

实盘执行默认关闭，必须同时满足账户授权、策略审批、风控检查、二次确认与 kill switch 可用才允许下单。任何放宽必须经过明确的产品决策与审批流程；数据延迟超限时禁止依赖该数据的策略下单。

**强制**：review — `tests/test_live_launch.py`、`tests/test_risk_engine.py`、`docs/06_SECURITY_RISK_COMPLIANCE.md`

## A11 时间与交易日口径显式

存储一律使用 UTC，交易所本地时区必须显式声明（A 股为 `Asia/Shanghai`）；禁止依赖进程默认时区。日线 bar 以交易所午夜打戳，公司行为在除权日开盘前应用，交易日历缺失时不得静默按自然日推算。

**强制**：automated — `tests/test_sessions.py`、`tests/test_calendars.py`、`packages/application/src/qtrade_application/backtesting.py`

## A12 依赖与工具链锁定

Python 固定 3.12、Node.js 22，依赖通过 `uv.lock` 与 `apps/web/package-lock.json` 锁定；CI 必须使用 `--locked` 与 `npm ci`。升级依赖需单独提交并说明验证结果，不得在功能提交中顺带升级运行时版本。

**强制**：automated — `uv.lock`、`apps/web/package-lock.json`、`.github/workflows/ci.yml`