# Paper 持久化账本验证（2026-10-09）

这是 OpenSpec `add-qtrade-phase1-mvp` 的 9.7 全链路任务的一部分，尚不构成 Paper 页面可用验收；8.10、8.11、9.1、9.7 均保持未完成。实际部署尚未组合此 Adapter，不能通过 API 手动提交订单绕过策略执行、资格和风控。

## 模块边界

`qtrade_domain.paper_matching` 是不依赖数据库的立即撮合计算；原内存 Paper Broker 和新的 `PostgresPaperBroker` 共用同一规则。默认滑点 5 bps、佣金 3 bps，不支持卖空。未达到成交条件的限价/止损订单明确拒绝，不模拟交易所排队、部分成交、A 股 T+1 或涨跌停规则。当前模型不能作为完整 A 股交易所模拟器的声明。

新 Adapter 只接受已持久化的 allow Risk Decision 和 submitted Order Execution Journal，并检查工作区、账户、策略版本及 running Paper Runtime。可信 Worker 提供价格函数；后续组合必须从运行时绑定的不可变 Dataset 提取价格，不能把浏览器提供的价格当行情。运行时资格和不可变数据绑定仍需完成。

一笔成交将余额、持仓、不可变回执和带 Intent trace_id 的系统审计在同一事务中提交。账户锁避免并发透支；工作区交易锁与 Kill Switch 启用/恢复共用，涵盖尚未创建开关行的并发场景。暂停/恢复等运行控制后续也必须遵循相同锁顺序。已提交回执先于运行状态/新价格检查返回，因此进程重启或后续暂停不会改变历史成交。

迁移 `0034_paper_broker.sql` 是前向增量，不修改原迁移。账户身份和初始配置不可修改；broker 回执禁止更新、删除和 truncate。Paper 账户不是外部 Broker 凭证或实盘账户。

## 真实数据库证据

```bash
.venv/bin/python scripts/selftest-postgres.py -k paper_ledger
.venv/bin/python scripts/selftest-postgres.py
.tools/uv/bin/uv run pytest
.tools/uv/bin/uv run mypy
.tools/uv/bin/uv run ruff check apps packages adapters tests
.tools/uv/bin/uv run bandit -r apps packages adapters -ll -q
```

10 个账本检查、63 个 PostgreSQL 集成检查通过。检查真实事务和迁移，行情价格和 runtime/策略资格是明确的测试输入，不是实际行情、策略执行或 UI 证据：

- 四个并发重试只生成一笔回执/持仓更新/审计；新 Adapter 实例及不同价格仍返回原成交。
- 两个不同 Intent 并发争用余额，一笔成交、一笔拒绝，无透支。
- 买入/卖出后现金、佣金、持仓清零和回执持久化正确；余额不足拒绝持久化且重试一致。
- paused/stopped/Live Runtime、缺少持久化授权、启用 Kill Switch 均拒绝且不改变余额。
- 审计失败导致余额、持仓、回执一起回滚；恢复审计后重试成功。
- 跨工作区查询、初始资金重配置、复用幂等键修改命令、无效价格均安全拒绝。

完整默认 Python 测试最后运行 634 通过、73 跳过（65 个 PostgreSQL 和 8 个 Timescale 外部检查）。账本提交时完整外部 PostgreSQL 63 项通过；随后新增的两项运行状态审计事务检查单独运行通过。类型检查 199 个源文件通过，Ruff、架构测试及 Bandit 中高严重等级检查通过。

运行状态迁移现在要求 Port 同时接收匹配的审计事实；PostgreSQL 将 pause/resume/stop 的状态版本与审计一起提交，并与账本/开关使用相同工作区锁顺序。实际数据库证明审计失败不改变状态、CAS 冲突不新增审计、错误审计目标不改变状态。这里仅保证状态迁移事务；取消订单/平仓外部副作用和停止前取消步骤仍需全链路组合与验收，不能将这项修复当 8.10 完成。

更新后的独立验收环境已应用迁移 34；真实浏览器 `cd apps/web && node tests/verification-risk.mjs` 通过：Kill Switch、独立用户恢复审批及刷新持久化。此检查覆盖现有风险 UI，未使用尚未组合的 Paper Adapter。

## 剩余验收

启动 API/页面 → 已验证策略版本/不可变行情绑定 → 持久化任务 → Worker/隔离 Runner 执行 → Intent → RiskEngine 允许/拒绝 → 此账本成交 → 页面持仓、订单、告警、审计及刷新；同时覆盖权限、确认、控制、重启恢复和桌面/手机。只有这条部署链路完成后才能勾选 9.7。
