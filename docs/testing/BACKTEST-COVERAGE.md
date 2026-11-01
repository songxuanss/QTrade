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
| 持久队列 | 真实 Worker | run `6b00d332-9a92-4d45-9ffb-170eb0e781a1` | API 只入队，Worker 认领后运行 |
| 现金分红/送转/拆并 | 已实现 | run `75e13372-7969-46f4-b27b-bc11c0037335` | 真实 600519 两次除息；送转/拆并单元测试 |
| T+1 可卖数量 | 已实现 | `test_mock_backtest_engine.py` | 当日买入不可卖，下一时段可卖 |
| 成员区间/幸存者偏差 | 真实 Worker | run `d6c3a7a2-6a26-425f-9dc5-2dcf0f5853c6` | 区间结束强制平仓、期末持仓 0、现金对账；区间为工作区定义而非官方历史 |
| tick size | 已实现 | `test_backtest_execution.py` | 买卖方向向不利方向取整；盘口仍未建模 |
| tick 数据集回测 | 真实逐笔 | run `701bab74-2e27-4358-ba14-ad5e47395fbb` | 真实 600519 逐笔发布为 tick 数据集，60s 聚合、现金对账 |
| Walk-forward 执行 | 真实 Worker | batch `f6311814-a419-4e11-852e-2116f832f865` | 9 个窗口全部完成，聚合与各窗口报告对账 |
| Walk-forward 参数寻优 | 真实 Worker | batch `ec634205-1fee-4aa1-923a-0da38863a4a1` | 每窗口 4 候选，样本内选择与验证结果独立复核 |
| 报告导出 | 真实下载 | run `9c33a542-94c7-4879-9ab1-22ee9646947f` | CSV/可打印 HTML 与持久化报告一致；刷新后一致；未登录 401 |
| 配股/复权因子 | 引擎真实价格 + fixture 事件 | `rights-factor-report.json` | 认购 30 股、现金对账；因子冻结核对 |
| 复权价回放 | 真实价格 + fixture 因子 | `vendor-adjusted-report.json` | 价格缩放 0.99；分配禁用，现金差额=股息 5163 |
| 订单类型/TIF | 真实 Worker | run `6232fa7e-e93a-4efc-ad0f-6e4599577c4b` | 止损入场、限价离场、day/FOK 撤销；现金对账 |
| 停牌/涨跌停锁价 | 真实数据 | run `659bf772-…`、`5b417815-…` | 停牌拒绝卖出并保留持仓；跌停锁价允许买入 |
| 市场冲击/队列 | 真实数据 | run `aa7ac7e7-…`、`ec552671-…` | 平方根冲击改变成交价并现金对账；锁价队列比例限制成交 |
| 全量 pytest | 全绿 | `full-pytest.txt` | 修复 bounded HTTP 子进程启动根因后 0 失败（详见 `docs/testing/BOUNDED-HTTP-FIX.md`） |
| 多 Worker/租约 | 真实双 Worker | `lease-fairness.json` | 并行运行 attempts=1、心跳续租、SIGKILL 后重新入队完成 |
| 协作式取消 | 真实双 Worker | `cooperative-cancel.json` | 运行中取消在块间中止，无结果对象，另一运行正常完成 |
| 导出 HTML/PDF/CSV | 未实现 | — | 报告只能页面查看 |
| 持久化 Worker 回测队列 | 已实现 | `backtest_run.lease_*`, Worker consumer | REST 重启后 queued 运行恢复 |

## 当前验证结果

- Python 聚焦测试：58 passed（backtesting、metrics、mock engine、isolated runner、execution、replay、quality）。
- Web ESLint / Prettier / TypeScript / Vitest：69 passed。
- Auth Playwright：10 passed + protected workspace 单测 1 passed；截图基线已更新。
- OpenSpec：`complete-quant-model-backtest` 与 `complete-professional-data-workbench` strict 校验通过。
- 真实回测：12 fills / 243 equity points / 2.07% total return / 0.55% max drawdown。

## 判定

- 真实量化模型 → 真实数据集 → 真实 Runner → 持久化结果 → UI 报告/对比链路已打通。
- 这证明“能实际运行模型并产出可复核回测”，不等于“专业 A 股全语义回测就绪”；官方历史成员区间、供应商因子数据和微观成交仍缺失。
- Fixture 仅覆盖受控取消/进度接口；真实 queued → running → completed、重启恢复和取消均使用 PostgreSQL + Worker + Runner。

## 复核命令

```bash
node /tmp/opencode/qtrade-real-backtest.mjs
node /tmp/opencode/qtrade-strategy-backtest-ux.mjs
node /tmp/opencode/qtrade-compare-selection.mjs
.venv/bin/python -m pytest tests/test_backtesting.py tests/test_backtest_metrics.py tests/test_mock_backtest_engine.py tests/test_isolated_strategy_program.py -q
```
