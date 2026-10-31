# 回测与量化模型覆盖矩阵（2026-11-01）

| 流程 | 状态 | 证据 | 说明 |
| --- | --- | --- | --- |
| 默认 SMA 模型创建 | 真实 UI | `.tools/verification-local/review/design/strategy-backtest-ux.json` | 新建策略使用默认源码/schema，Monaco 渲染通过 |
| 静态校验 | 真实 UI | 同上 | 默认模型静态校验通过 |
| 参数 schema 表单 | 真实 UI | 同上 | fast/slow/quantity 默认值可见 |
| 生命周期持久化 | 真实 UI | 同上 | 校验参数后刷新仍显示“配置回测” |
| 真实日线数据 | 真实来源 | `real-backtest-report.json` | 601288.SH 2025 全年，243 个采样点 |
| Runner 执行策略代码 | 真实 Runner | `real-backtest-result.json` | `qtrade.sandbox-event.v1`，12 个成交 |
| 冻结 manifest | 真实 API | `real-backtest-report.json` | 代码/数据/参数/seed 绑定 |
| 报告读取 | 真实 API/UI | `backtest-report-real-1440.png` | 指标、曲线、订单、成交、参数、哈希 |
| 运行历史刷新 | 真实 UI | 截图/运行列表 | 自动 4 秒刷新 + 手动刷新 |
| 选择对比 | 真实 UI | `backtest-compare-selected-1440.png` | 勾选 2 个完成运行，桌面/手机无溢出 |
| 取消/进度 | Fixture | `login.spec.ts` | 未接真实 Worker 队列 |
| 复权/分红/拆并股 | 未实现 | — | 报告与表单显示限制 |
| T+1 可卖数量 | 未实现 | — | A 股日内回转仍可能通过 |
| 历史成分/退市 | 未实现 | — | 存在幸存者偏差 |
| tick size/盘口 | 未实现 | — | 使用 bps 滑点与成交量上限 |
| Walk-forward 执行 | 仅计划 | `walk-forward` UI | 没有逐窗口运行和汇总 |
| 导出 HTML/PDF/CSV | 未实现 | — | 报告只能页面查看 |
| 持久化 Worker 回测队列 | 未实现 | — | 本地 API 同步执行 |

## 当前验证结果

- Python 聚焦测试：58 passed（backtesting、metrics、mock engine、isolated runner、execution、replay、quality）。
- Web ESLint / Prettier / TypeScript / Vitest：69 passed。
- Auth Playwright：10 passed + protected workspace 单测 1 passed；截图基线已更新。
- OpenSpec：`complete-quant-model-backtest` 与 `complete-professional-data-workbench` strict 校验通过。
- 真实回测：12 fills / 243 equity points / 2.21% total return / 0.54% max drawdown。

## 判定

- 真实量化模型 → 真实数据集 → 真实 Runner → 持久化结果 → UI 报告/对比链路已打通。
- 这证明“能实际运行模型并产出可复核回测”，不等于“专业 A 股全语义回测就绪”；复权、T+1、历史成分和微观成交仍缺失。
- Fixture 只覆盖取消/进度等尚未 Worker 化的生命周期，不计真实来源验收。

## 复核命令

```bash
node /tmp/opencode/qtrade-real-backtest.mjs
node /tmp/opencode/qtrade-strategy-backtest-ux.mjs
node /tmp/opencode/qtrade-compare-selection.mjs
.venv/bin/python -m pytest tests/test_backtesting.py tests/test_backtest_metrics.py tests/test_mock_backtest_engine.py tests/test_isolated_strategy_program.py -q
```
