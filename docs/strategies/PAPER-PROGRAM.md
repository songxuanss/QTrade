# 隔离策略回调协议

此协议用于连接 OpenSpec 9.7 的策略执行部分。当前尚未组合 Paper 启动页面、任务和订单主干，以下代码不是已可在页面启动交易的功能声明。

策略输出 Strategy Signal；应用层负责绑定工作区、运行时、账户和不可变策略版本，持久化 Order Intent，再调用风控。策略不能指定 Command ID、幂等键、风险允许结果或 Broker 凭证。

## 示例

```python
from qtrade import Strategy

class BuyOnce(Strategy):
    def on_start(self):
        self.seen = 0

    def on_bar(self, bar):
        self.seen += 1
        if self.seen == self.parameters["when"]:
            self.buy(self.parameters["quantity"])
```

源码恰好定义一个 `Strategy` 子类，不需要自行打印结果。`on_start` 在参数注入后执行；`on_bar` 按历史顺序执行。`bar` 含标准 Bar 字段，价格与量为 Decimal，timestamp 为带时区时间。

`buy(quantity, **options)` 和 `sell(quantity, **options)` 产生提议；完整形式为 `order(side, quantity, *, instrument_id=None, order_type="market", limit_price=None, stop_price=None, time_in_force="day")`。默认使用当前 Bar 的标的。支持 market/limit/stop/stop_limit 提议，限价和止损字段必须与类型相符；实际 Broker 支持情况和风险资格另行校验。数量、价格必须有限且为正，不接受执行身份字段或数据集中不存在的标的。

## 隔离与恢复

`StrategyProgramPort.evaluate(code, history, parameters)` 隐藏隔离进程和通信；客户端本身不执行策略。`IsolatedStrategyProgram` 只能通过绝对路径 Unix Socket 到 Runner。现有 API 没有该 Socket 挂载；调用来自 Worker。Runner 的只读容器、断网、无密钥环境、随机 UID/GID、后代清理及资源限制保持不变。
客户端仅传递当前 OpenTelemetry traceparent；Runner 的内部 Socket 监听显式接收父 Trace，公开 API 默认不接受来访 Trace 作为父级。baggage 和 tracestate 不跨进程传递，Runner 返回的 X-QTrade-Trace-ID 应与 Worker 操作一致。

每次求值从运行开始重放一个完整的历史前缀，只返回最后一根 Bar 上的提议；不传入未来 Bar，不截断历史来假装保留状态。进程重启可以重建计数器等内部状态。随机数种子固定为 0；这不保证使用系统时间等外部非确定性行为的代码可复现。应用必须先保存 Intent，再执行订单，避免重试重新生成不同提议。

此初始协议单次最多 1000 根历史 Bar，输入 1 MB、输出 64 KiB，每根最多 100 条提议；这些是隔离执行约束，不影响已配置为不限的下载日期范围。资源不足时明确失败，不静默缩短历史。Runner 禁用 site packages，当前仅支持标准库和 `qtrade.Strategy` 桥；声明第三方依赖并不意味着已安装。后续引擎 Adapter 必须保持契约且显式声明支持的运行配置。

策略输出与 SDK 诊断不直接进入应用错误、审计和日志；客户端只接受有界的标准提议数据及固定错误分类。无效/负数/非有限提议、未知标的、外来身份字段、错误输入顺序、跨数据集历史和 suspect/quarantined 行情均拒绝。
数值采用普通十进制文本，最多 28 位有效数字，并限制编码长度/小数指数，避免策略提议在 Worker 中触发超大数字展开或超出标准 Decimal 算术预算。交易金额与仓位限额仍由 RiskEngine 决定。

## 验证

单元验证的 HTTP 传输是明确替身，测试不会在 pytest 进程中执行策略：

```bash
.tools/uv/bin/uv run pytest tests/test_isolated_strategy_program.py tests/test_architecture.py
```

部署后运行实际 Worker 容器→隔离 Runner 检查，使用已经下载的 `600519.SH` 五根 2025-09-01 至 2025-09-05 日线；验证参数、回调状态重放、重建 Adapter 一致性、非法提议、运行异常、资源限制及随后正常作业，并确认 API 容器不能访问 Runner Socket：

```bash
.venv/bin/python scripts/verification-strategy-program.py
```

该检查不包含页面、启动资格、风控、成交或告警，因此不能单独勾选 9.7。Nautilus 已选定的引擎接入验收也独立保留；回调协议不是 Nautilus 撮合或 Tick 回放可用的证明。

2026-10-09 重新部署后的检查通过：实际 Worker 操作与 Runner 返回同一个 Trace ID，真实归档 Bar 的策略回调、参数、状态重放、资源失败后恢复通过，API 容器访问 Socket 被拒绝。`bash scripts/selftest-runner.sh` 的完整隔离回归也通过，包括脱离会话的后代清理与回收。

Socket 调用在实际 Worker 容器内执行，但尚不是后台任务队列消费；客户端协议检查使用 HTTP 替身，真实 Runner 检查单独记录。默认 Python 完整测试 662 项通过、73 项外部检查跳过；mypy 202 个源文件、Ruff 和架构检查通过。数字不能替代页面主干验收；后续组合仍须逐项验证。

## Causal replay schedule

`StrategyReplayService` builds an ordered proposal schedule by passing only each
complete history prefix to `StrategyProgramPort`. It binds the result to hashes
of exact source, canonical parameters and canonical bars. Parameters are copied
for every callback; mixed datasets, duplicate events, unavailable instruments
and failed callbacks cannot produce an accepted partial schedule. Source still
executes only inside the restricted Runner.

This schedule is input for the trusted engine adapter, not an authorized Order
Intent or a fill. The engine must schedule proposals at the actual availability
of each close; start-labelled daily bars do not make closing prices available
at their timestamp. See [Nautilus timing verification](../testing/NAUTILUS-TIMING.md).

Committed image `07c85c0` passed the actual Worker/Runner check using five
archived MaiRui daily bars. A counter strategy emitted exactly one proposal at
history index 2; a fresh client replay with reordered parameter keys produced
the identical input hashes and schedule. Shared tracing, invalid proposals,
resource failure recovery and denied API socket access also passed. This is
container integration evidence; queue consumption, fills and UI remain pending.
The full Python suite passed 670 tests with 73 external checks skipped; mypy
passed 203 source files, and focused Ruff and Bandit checks passed.

