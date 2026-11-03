# 策略创作护栏（L0+L1）验收记录

范围：`openspec/changes/add-strategy-authoring-guardrails`。只改编辑体验与模板库，不触及 API、Worker、Runner、manifest 或回测执行语义。

## 交付内容

| 能力 | 位置 |
| --- | --- |
| 15 个官方模板（趋势 6 / 反转 3 / 横截面 5 / 基准 1） | `apps/web/src/features/strategies/templates.json` |
| 模板唯一生成源（含 `--check`） | `scripts/generate-strategy-templates.py` |
| 模板类型包装、查找与分组 | `apps/web/src/features/strategies/templates.ts` |
| 参数 schema 表单/JSON 双向编辑 | `apps/web/src/features/strategies/schema-editor.ts` |
| Monaco 补全与悬停文档 | `apps/web/src/features/strategies/strategy-language.ts` |
| 模板分组面板、表单渲染、内联 marker | `apps/web/src/features/strategies/workbench.tsx` |
| 样式（含 720px 以下重排） | `apps/web/src/app/globals.css` |
| 模板静态校验、schema 规范化与生成器一致性 | `tests/test_strategy_templates.py` |
| schema 编辑器纯函数测试 | `apps/web/tests/unit/schema-editor.test.ts` |
| 真实浏览器验收（15 模板逐一切换） | `apps/web/tests/verification-strategy-authoring.mjs` |
| 15 模板真实回测 | `scripts/verification-strategy-templates.py` |

## 模板库

`templates.json` 由 `scripts/generate-strategy-templates.py` 生成，Web UI 与 Python 测试读取同一份文件，`tests/test_strategy_templates.py::test_templates_file_matches_generator` 断言两者一致，避免手改漂移。

| 分组 | 模板 | 经济含义 |
| --- | --- | --- |
| 趋势 | 双均线交叉 | 均线金叉/死叉的趋势跟随最小实现 |
| 趋势 | 时序动量 | 标的自身回看收益转正持有、转负离场 |
| 趋势 | 唐奇安通道突破 | 海龟法则：突破 N 日高、跌破 M 日低 |
| 趋势 | MACD 趋势 | 快慢 EMA 差与信号线交叉 |
| 趋势 | ATR 吊灯止损 | 均线入场 + ATR 倍数移动止损 |
| 趋势 | 区间新高动量 | 52 周新高效应 + 长期均线过滤 |
| 反转 | RSI 均值回归 | 超卖买入、超买卖出 |
| 反转 | 布林带均值回归 | 跌破下轨买入、回归中轨卖出 |
| 反转 | RSI 趋势过滤 | 只在长期均线上方做超卖买入 |
| 横截面 | 横截面动量 | 按 N 日涨幅取前 K 名轮动 |
| 横截面 | 双动量轮动 | 绝对动量为正的前 K 名，否则空仓 |
| 横截面 | 波动率目标动量 | 风险调整动量选股 + 目标波动率缩放仓位 |
| 横截面 | 横截面低波 | 持有波动率最低的 K 只 |
| 横截面 | 横截面短期反转 | 买入近期跌幅最大的 K 只 |
| 扫地僧 | 龙回头 | 强势股缩量回踩均线低吸，反弹/破位/超时离场 |
| 扫地僧 | 量价齐升 | 放量突破近期高点且均线向上时跟进 |
| 扫地僧 | 均线粘合突破 | 均线带宽收窄后突破前高视为方向选择 |
| 扫地僧 | 跳空不补 | 放量跳空高开收阳后跟随，回补缺口离场 |
| 扫地僧 | 网格低吸 | 每跌一格买入、每涨一格卖出一份 |
| 扫地僧 | KDJ 金叉 | 低位金叉买入、高位死叉卖出 |
| 基准 | 等权基准 | 各标等额买入持有，作为被动对照 |

### 「扫地僧」分类的来源与边界

这一组不是学术因子，而是公开量化社区里反复出现的实战规则形态（龙回头、量价齐升、均线粘合、跳空不补、网格、KDJ 金叉）。它们在本项目里被改写成只依赖日线 OHLCV 与持仓状态的确定性规则，并沿用同一套撮合与费用模型：

- 龙回头/趋势低吸：东方财富、雪球等社区文章描述的“前期强势 + 缩量回踩支撑”形态。
- 量价齐升：BigQuant 社区作业与掘金量化文章里常见的“放量突破、跌破短均线离场”。
- 均线粘合突破：社区讨论的均线收敛后方向选择，常与放量条件组合。
- 跳空不补：以缺口下沿作为止损线的短线跟随规则。
- 网格低吸：震荡市里用固定间距分批买卖，不预测方向。
- KDJ 金叉：掘金量化文章直接给出“KDJ 高位卖出、MACD 低位金叉买入”一类规则。

同时必须说明：社区热度不等于有效。公开讨论里既有长期实盘总结，也有被证伪的“霸榜策略”（例如 2026 年量化打假文章对“ST 弱转强”的回测口径质疑）。因此该分组在界面上明确标注“未经严谨学术检验”，默认参数只用于让流程跑通，不构成任何策略有效性结论。

多标的模板全部使用**逐标的字典状态**。早期 `sma-cross` / `rsi-reversal` 用单组标量变量保存 `closes` 与 `holding`，在 20 只标的数据集上会串味：同一时间窗口内不同标的互相覆盖状态，产生 1059 单只成交 13 笔的失真结果。改为按 `instrument_id` 分桶后同一模板为 258 单 / 172 成交。

## 设计边界

- 模板只填充源码、schema、名称与说明。保存后版本仍由既有流程冻结，`code_sha256` 覆盖实际执行内容，模板元数据不进入 manifest。
- 表单视图与 JSON 视图指向同一份序列化 JSON；解析失败时保留原文并提示，不清空用户输入。
- Monaco 补全与悬停是静态提示，不参与判定；拒绝仍由服务端 `validate_strategy_source` 与隔离 Runner 完成。
- 模板源码继续受既有静态规则约束（禁止导入白名单外模块、禁止动态执行、`range()` 必须为静态整数边界等）。

## 复跑命令

```bash
.venv/bin/python scripts/generate-strategy-templates.py --check
.venv/bin/python -m pytest tests/test_strategy_templates.py -q
cd apps/web && npx vitest run tests/unit/schema-editor.test.ts
cd apps/web && npm run lint && npm run typecheck && npm run build
.venv/bin/python scripts/check_constitution.py
```

真实浏览器验收（需先启动本地栈并由生产构建提供 Web）：

```bash
cd apps/web
PLAYWRIGHT_BROWSERS_PATH=../../.tools/ms-playwright \
LD_LIBRARY_PATH=../../.tools/browser-deps/root/usr/lib/x86_64-linux-gnu:../../.tools/browser-deps/root/lib/x86_64-linux-gnu \
NODE_PATH=./node_modules \
node tests/verification-strategy-authoring.mjs
```

15 模板真实回测（需本地栈在线）：

```bash
.venv/bin/python scripts/verification-strategy-templates.py
```

## 验收结果

### 自动化

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 生成器一致性 | `python scripts/generate-strategy-templates.py --check` | `templates.json is current` |
| 模板静态校验与 schema 规范化 | `pytest tests/test_strategy_templates.py -q` | 6 passed |
| 全量 Python 回归 | `pytest -q -p no:randomly` | 1727 collected，exit 0，无失败 |
| schema 编辑器单测 | `npx vitest run tests/unit/schema-editor.test.ts` | 6 passed |
| 前端全量单测 | `npx vitest run` | 16 files / 78 tests passed |
| ESLint | `npx eslint src/features/strategies tests/verification-strategy-authoring.mjs` | 通过 |
| TypeScript | `npx tsc --noEmit` | 通过 |
| 生产构建 | `npm run build` | 通过 |
| 宪法门禁 | `python scripts/check_constitution.py` | Constitution checks passed |
| Python lint/format | `ruff check` / `ruff format --check` | 通过 |

### 真实浏览器（Playwright，生产构建 + 本地真实栈）

脚本 `apps/web/tests/verification-strategy-authoring.mjs`，证据目录 `.tools/verification-local/strategy-authoring/`。

| 步骤 | 结果 |
| --- | --- |
| 登录并打开策略工作台 | 通过 |
| 模板面板渲染 21 个模板、5 个分组 | 通过（`strategy-template-card` = 21，分组 = 趋势/反转/横截面/扫地僧/基准） |
| 选择“横截面动量”后源码、名称、参数表单同步 | 通过（Monaco model 含 `class MomentumRank(Strategy)`；schema 行 = 5） |
| `self.` 触发补全并列出 `parameters` / `buy` / `order` | 通过 |
| 悬停 `buy` 显示方法签名 | 通过 |
| 21 个模板逐一应用、schema 行数与源码逐字匹配、各自通过服务端静态校验 | 通过 |
| 表单把 `top_n` 默认值改为 3 | 通过（JSON 视图出现 `"default": 3`） |
| 插入 `import os` 触发静态校验 | 通过（`capability.*` 诊断；Monaco marker 落在第 1 行，截图 `desktop-inline-diagnostic.png`） |
| 移除违规导入后静态校验通过 | 通过 |
| 创建策略 → 保存为新版本 | 通过（`strategyId` `0a5fe512-ed7b-4179-8a12-88ba1c2be327`） |
| 参数校验 → “配置回测”可用并跳转 | 通过 |
| 刷新后版本与参数 schema 仍在 | 通过（`default: 3` 已持久化，截图 `desktop-strategy-persisted.png`） |
| 桌面 1440 / 手机 390 无横向溢出 | 通过（记录在 `report.json.viewports`） |
| 手机端模板与表单可用 | 通过（截图 `mobile-templates.png`、`mobile-schema-form.png`） |
| 控制台无错误 | 通过（`report.json.consoleErrors` 为空） |

### 真实回测（21 模板全部进入既有执行链路）

脚本 `scripts/verification-strategy-templates.py`：为每个模板创建或修订持久化策略，冻结为不可变版本，转为 `validated`，再用模板 schema 默认值在数据集 `4ad3c536-681b-57d3-b2f1-7fc224093106`（20 只成分股、4840 bar、2025-10-30T16:00Z → 2026-10-30T16:00Z，日线）上创建真实回测，由 Worker 与隔离 Runner 执行，完成后转 `backtested`。证据 `.tools/verification-local/strategy-authoring/templates-backtest.json`；`--only id1,id2` 可只复跑指定模板。

| 模板 | run_id | 订单 / 成交 | 区间收益 | 最大回撤 |
| --- | --- | --- | --- | --- |
| 双均线交叉 | `86159d1e-9d3b-4b3d-903d-2886df4da6a8` | 258 / 172 | +21.59% | 14.69% |
| 时序动量 | `46da7fff-132e-4ce7-8bcf-7e50444e674c` | 81 / 43 | −7.96% | 24.20% |
| 唐奇安通道突破 | `db9fceca-fdfb-495a-a741-d8f396e97626` | 58 / 44 | +5.53% | 15.35% |
| MACD 趋势 | `8b40139c-f62b-482b-9a95-df752e629492` | 346 / 223 | +10.83% | 15.04% |
| ATR 吊灯止损 | `3c6781ab-0439-4236-b1e4-ef557180b3a0` | 125 / 95 | +1.06% | 17.89% |
| 区间新高动量 | `20ccf416-9b4f-421a-ae08-58811bd585f9` | 71 / 58 | +5.84% | 14.44% |
| RSI 均值回归 | `8424d254-ff2f-4b72-8286-7b4c68d24aa8` | 42 / 34 | +12.22% | 9.50% |
| 布林带均值回归 | `8d403a26-a7d7-4589-b541-b975e7d6f812` | 132 / 100 | −19.33% | 19.56% |
| RSI 趋势过滤 | `2801a9d4-6356-4602-84f1-14c38c447449` | 22 / 21 | +0.07% | 3.97% |
| 横截面动量 | `14aae69c-245b-4ba7-af98-b6eddff42e1f` | 42 / 37 | −13.69% | 20.49% |
| 双动量轮动 | `9f4b0997-33e4-4e8c-b0b4-11e49da2af16` | 15 / 13 | −12.63% | 14.49% |
| 波动率目标动量 | `5999114b-c360-4109-9336-6fe3a0b17b33` | 31 / 27 | −1.50% | 6.73% |
| 横截面低波 | `b9448b75-f518-4af7-8663-d252d3128a4a` | 18 / 17 | +5.14% | 6.73% |
| 横截面短期反转 | `e038dde5-47f0-4344-a6c1-4a7a2a8f8365` | 220 / 217 | −1.39% | 11.40% |
| 龙回头 | `f84429a7-8844-4c65-8a48-b915cc0ad19e` | 12 / 12 | −0.64% | 1.05% |
| 量价齐升 | `f3e92096-5dbf-42aa-a51f-a0c4f57c23dc` | 87 / 85 | +2.43% | 3.76% |
| 均线粘合突破 | `2e14f158-9128-4b72-9dce-cab1b6e62669` | 122 / 121 | −0.18% | 6.09% |
| 跳空不补 | `3ca3e541-250a-4603-bef3-8e531c002081` | 62 / 61 | +2.59% | 2.58% |
| 网格低吸 | `0426c0f9-4a69-4347-a317-c5753dbc12f6` | 142 / 142 | +3.79% | 1.97% |
| KDJ 金叉 | `f074c04e-8d45-4ec8-a4db-a38ca980c83d` | 103 / 103 | −0.18% | 8.24% |
| 等权基准 | `8b2551bc-56f0-4a46-8332-8e33eb0e815f` | 20 / 20 | +7.77% | 9.56% |

全部 21 个回测 `status=completed`，订单、成交与 242 个权益点均非空，版本生命周期已完成 `draft → validated → backtested`。其中「扫地僧」6 个模板首次回测即有成交（12–142 单），没有为了收益做参数搜索；参数只保证规则在该数据集上可运行。

参数默认值经过一次可复核的调参：`rsi-trend-filter` 初始 `trend_window=60, oversold=35` 在本样本上 0 单，改为 `trend_window=120, oversold=40` 后为 22 单 / 21 成交；`equal-weight` 单只金额从 150000 改为 50000（20 只 × 5 万 ≈ 100 万初始资金），否则基准会因资金不足只成交 8/20。这两处都是为了让默认模板在验收数据集上真正跑通，不代表策略优选。

### 浏览器创建的模板策略单独回测

脚本 `scripts/verification-strategy-template-run.py`：读取浏览器流程保存的“横截面动量”策略版本，使用表单修改后的 `top_n=3` 在 `4ad3c536-681b-57d3-b2f1-7fc224093106` 上创建真实回测。

| 项目 | 值 |
| --- | --- |
| run_id | `775fa0bd-a309-4c8c-8411-029a6135ccd4` |
| 策略版本 | `87fb6b21-f011-47f8-b8a8-7021e80fa44e` v1（`strategy_version_id` `4037b745-27d3-456d-884f-7045234f37d6`） |
| 参数 | `lookback=60, top_n=3, rebalance_every=20, position_value=150000, lot_size=100` |
| 订单 / 成交 | 29 / 25 |
| 权益点 | 242 |
| 初始 / 期末权益 | 1,000,000.00 → 889,644.10 |
| 区间收益 / 最大回撤 | −11.04% / 15.11% |

对照：同一模板用 `top_n=5` 的默认值时订单 42、成交 37（run `11c797e5-0463-4bad-9020-686226b5f798`）。参数变化改变了下单数量，说明表单编辑的是真正参与执行的版本 schema，而不是仅前端展示。

### 数据采集与回测全链路护栏实证

| 验证项 | 真实证据 |
| --- | --- |
| 含第三方依赖版本入队前阻断 | 策略 `d6eb109c-6b8f-4f2d-926d-586dca6f8c6f` v1（`dependencies={"numpy":"==2.1.0"}`）提交回测时由 `BacktestService.create` 直接返回 HTTP 422 `unsupported workspace request`，不再创建必然失败的排队运行 |
| `CNY` 空 `execution_model={}` 自动补齐 A 股默认参数 | `sma-cross` v6 在 `4ad3c536-681b-57d3-b2f1-7fc224093106` 上以 `execution_model={}` 提交 run `c7a39951-2b79-4f3c-899f-303170959014`，自动冻结 100 股整手、T+1、佣金 3bps/min 5、印花税 5bps、滑点 5bps 与 `periods_per_year=252`，产出 258 单 / 172 成交、首笔费用 `45.1641`、年化 `21.67%`、Sharpe `1.1035`，与显式 A 股参数逐位一致 |
| 多周期多标的数据集真实合成与回测 | 股票池 `6d2e7db1-38c5-4698-8bb9-befdab34e8ec`（上证公用）在 `1h` 粒度合成 5 标的集 `b8843e1c-23e1-5c8b-ad75-5ed1d66405f0` 并完成真实回测 run `16ace92d-17d6-42a9-beba-58628c44528c`（40 单 / 36 成交 / 160 权益点，Sharpe `0.4620`） |
| CSV 导入自定义股票池 → 合成 → 回测深链 | Playwright 上传 `pool.csv` 自动补全 `000001.SZ` 并生成快照 `ba2b88f7-c6cd-4a4b-a78f-b3d8121040ff`，点击“前往合成选股数据集”切换至合成面板并预选该快照，点击“前往回测此数据集”直达 `/workspace/backtests?dataset_id=4ad3c536-...` 并预选 20 标的合成集 |

### 结论边界

该样本为 20 只成分股、约 12 个月的单一行情区间，且未复权行情叠加显式公司行为账本。收益正负只说明“模板可执行、结果可复核”，不构成任何策略有效性结论，也不代表更长周期或其它股票池的表现。