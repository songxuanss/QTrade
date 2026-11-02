# 回测与量化模型覆盖矩阵（2026-11-02）

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
| 逐笔价位量深度 | 真实逐笔 | run `b22ea6fa-c52f-4e56-8256-a6168fc1fca8` | 窗口 VWAP 独立重算一致、花费对账、`depth_model` 冻结 |
| Walk-forward 执行 | 真实 Worker | batch `f6311814-a419-4e11-852e-2116f832f865` | 9 个窗口全部完成，聚合与各窗口报告对账 |
| Walk-forward 参数寻优 | 真实 Worker | batch `ec634205-1fee-4aa1-923a-0da38863a4a1` | 每窗口 4 候选，样本内选择与验证结果独立复核 |
| 报告导出 | 真实下载 | run `9c33a542-94c7-4879-9ab1-22ee9646947f` | CSV/可打印 HTML 与持久化报告一致；刷新后一致；未登录 401 |
| 配股/复权因子（fixture） | 引擎真实价格 + fixture 事件 | `rights-factor-report.json` | 认购 30 股、现金对账；因子冻结核对 |
| 公开页配股条款（真实双源） | 真实采集 + 发布 + Worker 回测 + UI | publication `95d60447-4d8e-5bc9-b11e-17380d690ee4`，dataset `fd2f8bb3-d391-5b84-b7d9-953f5cc64ac6`，runs `d6b60d0f-ee85-4225-b635-938651682f98`（subscribe）/`c3e1b6d1-d677-4927-9a66-0671f4219c71`（skip） | 300176.SZ 2026 年 4 配 10 @ 3.36；除权日认购 4000 股、期末现金 `933360.00`；skip 保留 `946800.00`；CSV/HTML 与桌面/手机浏览一致；来源仅内部研究、不可再分发，Wind 未接入 |
| 策略版本 qualification | 真实 Worker + 持久化 Run | `test_strategy_qualification.py`，run `d6b60d0f-…` | `draft→validated→backtested`；`backtest_succeeded` 仅由精确版本的完成 Run 推导，调用方无法伪造 |
| 选股数据集合成（API） | 真实快照 + 已发布单标的数据集 | dataset `4ad3c536-681b-57d3-b2f1-7fc224093106` | 上证央企 50 成员 → 按 4 周平均成交额取 20；4,840 根、`quality={valid:4840}` |
| 选股数据集合成（UI） | 真实 UI + 持久化 | `ui-strategy-portfolio/ui-results.json` | 桌面/手机可见并提交；重复合成幂等返回同一 dataset；桌面/手机无横向溢出 |
| 多策略组合回测 | 真实 Worker，5 策略 + 基准全完成 | `strategy-portfolio-report.json` | 6 个 Run 全部 `completed`/`backtested`，有订单、成交、权益曲线与导出 |
| 策略横向比对 | 真实 compare API | `strategy-portfolio-report.json` | `comparable=true`、`warnings=[]`；唯一输入差异为 `strategy_version_id` |
| 对比页（桌面/手机） | 真实 UI | `compare-ui-results.json`、`desktop-07-compare.png`、`mobile-04-compare.png` | 6 条归一权益路径与指标差异表；显示“输入范围可直接比较”；无横向溢出 |
| 参数灵活定制 | 真实 Worker | `portfolio-parameter-variation.json` | 同版本改 `lookback=40/top_n=3/rebalance=10`：-14.38%、MDD 28.32%、换手 10.58× |
| 同参数可复现 | 真实 Worker | 同上 | 两次运行权益曲线 SHA-256 相同、全部指标逐项相同 |
| 低波动/反转行业共识核对 | 真实结果 | 同上 | 低波波动率/回撤最低；反转换手最高；蓝筹震荡年份反转/趋势为负、基准为正 |
| UI 新建回测（桌面） | 真实 UI → Worker | Run `fbc031e3-cba3-47ad-8324-2cd8cddf0e13` | v4 动量、1,000,000 初始资金；指标与 API Run `71f016b4-…` 完全一致 |
| 报告导出（桌面真实下载） | 真实下载 | `ui-strategy-portfolio/qtrade-backtest-71f016b4-20251030.csv/html` | CSV 39,104 字节、HTML 29,478 字节；刷新后报告不变 |
| 组合结果（手机） | 真实 UI | `mobile-01-composition.png`、`mobile-03-report.png` | 合成面板与报告可读，无页面级横向溢出 |
| 覆盖不足拒绝 | 真实 API | `portfolio-rejections.json` | 2 成员池取 20 → 400 |
| 5000 根预算拒绝 | 真实 API | `portfolio-rejections.json` | 50 成员 × 242 根 = 12,100 → 400 |
| 未完成 Run 转 backtested | 真实 API | `portfolio-rejections.json` | 无完成 Run → 403；qualification 不可伪造 |
| 非管理员合成/回测 | 真实 API（临时 viewer） | `portfolio-rejections.json` | 合成 → 403、回测 → 403；`finally` 恢复 admin |
| Runner 批量预算 | 单元回归 + 真实运行 | `test_isolated_strategy_program.py`、`test_strategy_runner.py` | 8 MB 输入、1 MiB 输出与 5,000 bar 批量上限配套 |
| 复权价回放 | 真实价格 + fixture 因子 | `vendor-adjusted-report.json` | 价格缩放 0.99；分配禁用，现金差额=股息 5163 |
| 真实供应商复权因子 | 真实供应商采集 | publication `38b89f8c-…`，run `824f6854-…` | 3 条因子与真实除息日一致；价格按因子比例缩放；现金对账 |
| 订单类型/TIF | 真实 Worker | run `6232fa7e-e93a-4efc-ad0f-6e4599577c4b` | 止损入场、限价离场、day/FOK 撤销；现金对账 |
| 停牌/涨跌停锁价 | 真实数据 | run `659bf772-…`、`5b417815-…` | 停牌拒绝卖出并保留持仓；跌停锁价允许买入 |
| 市场冲击/队列 | 真实数据 | run `aa7ac7e7-…`、`ec552671-…` | 平方根冲击改变成交价并现金对账；锁价队列比例限制成交 |
| 全量 pytest | 全绿 | `full-pytest.txt` | 修复 bounded HTTP 子进程启动根因后 0 失败（详见 `docs/testing/BOUNDED-HTTP-FIX.md`） |
| 多 Worker/租约 | 真实双 Worker | `lease-fairness.json` | 并行运行 attempts=1、心跳续租、SIGKILL 后重新入队完成 |
| 协作式取消 | 真实双 Worker | `cooperative-cancel.json` | 运行中取消在块间中止，无结果对象，另一运行正常完成 |
| 持久化 Worker 回测队列 | 已实现 | `backtest_run.lease_*`, Worker consumer | REST 重启后 queued 运行恢复 |

## 当前验证结果

- 组合验收：20 只 × 242 根 = 4,840 根真实数据集；6 个策略全部完成并转 `backtested`；compare `comparable=true`。
- 组合指标：低波 +6.55%（MDD 6.93%、波动 9.12%、Sharpe 0.77）；风险调整动量 +7.87%；等权基准 +7.17%；动量 +0.40%；趋势 -19.77%；反转 -24.48%。
- 真实导出：CSV 39,104 字节、HTML 29,478 字节，内容与持久化报告一致。
- 拒绝路径：覆盖不足 400、预算超限 400、未完成 Run 转 `backtested` 403、viewer 合成/回测 403，全部实测。
- Web ESLint / TypeScript / build 通过；Playwright 桌面 1440×1000 与手机 390×844 无控制台错误。
- 早期单标的回测（601288.SH）：12 fills / 243 equity points / 2.07% total return / 0.55% max drawdown。

## 判定

- 真实量化模型 → 真实数据集 → 真实 Runner → 持久化结果 → UI 报告/对比链路已打通。
- 这证明“能实际运行模型并产出可复核回测”，不等于“专业 A 股全语义回测就绪”；官方历史成员区间、Wind 等商业授权、L2 盘口与微观成交仍缺失。
- Fixture 仅覆盖受控取消/进度接口；真实 queued → running → completed、重启恢复和取消均使用 PostgreSQL + Worker + Runner。

## 复核命令

```bash
node /tmp/opencode/qtrade-real-backtest.mjs
node /tmp/opencode/qtrade-strategy-backtest-ux.mjs
node /tmp/opencode/qtrade-compare-selection.mjs
.venv/bin/python -m pytest tests/test_backtesting.py tests/test_backtest_metrics.py tests/test_mock_backtest_engine.py tests/test_isolated_strategy_program.py -q
```
