# 策略创作护栏（L0+L1）验收记录

范围：`openspec/changes/add-strategy-authoring-guardrails`。只改编辑体验，不触及 API、Worker、Runner、manifest 或回测执行语义。

## 交付内容

| 能力 | 位置 |
| --- | --- |
| 五个官方模板（双均线、RSI 反转、横截面动量、横截面低波、等权基准） | `apps/web/src/features/strategies/templates.json` |
| 模板类型包装与查找 | `apps/web/src/features/strategies/templates.ts` |
| 参数 schema 表单/JSON 双向编辑 | `apps/web/src/features/strategies/schema-editor.ts` |
| Monaco 补全与悬停文档 | `apps/web/src/features/strategies/strategy-language.ts` |
| 模板面板、表单渲染、内联 marker | `apps/web/src/features/strategies/workbench.tsx` |
| 样式（含 720px 以下重排） | `apps/web/src/app/globals.css` |
| 模板静态校验与 schema 规范化 | `tests/test_strategy_templates.py` |
| schema 编辑器纯函数测试 | `apps/web/tests/unit/schema-editor.test.ts` |
| 真实浏览器验收 | `apps/web/tests/verification-strategy-authoring.mjs` |

## 设计边界

- 模板只填充源码、schema、名称与说明。保存后版本仍由既有流程冻结，`code_sha256` 覆盖实际执行内容，模板元数据不进入 manifest。
- 表单视图与 JSON 视图指向同一份序列化 JSON；解析失败时保留原文并提示，不清空用户输入。
- Monaco 补全与悬停是静态提示，不参与判定；拒绝仍由服务端 `validate_strategy_source` 与隔离 Runner 完成。
- 模板源码继续受既有静态规则约束（禁止导入白名单外模块、禁止动态执行、`range()` 必须为静态整数边界等）。

## 复跑命令

```bash
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

## 验收结果

### 自动化

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 模板静态校验与 schema 规范化 | `pytest tests/test_strategy_templates.py -q` | 5 passed |
| 全量 Python 回归 | `pytest -q -p no:randomly` | 1726 collected，exit 0，无失败 |
| schema 编辑器单测 | `npx vitest run tests/unit/schema-editor.test.ts` | 6 passed |
| 前端全量单测 | `npx vitest run` | 16 files / 78 tests passed |
| ESLint | `npx eslint src/features/strategies tests/unit/schema-editor.test.ts` | 通过 |
| TypeScript | `npx tsc --noEmit` | 通过 |
| 生产构建 | `npm run build` | 通过 |
| 宪法门禁 | `python scripts/check_constitution.py` | Constitution checks passed |
| Python lint/format | `ruff check` / `ruff format --check` | 通过 |

### 真实浏览器（Playwright，生产构建 + 本地真实栈）

脚本 `apps/web/tests/verification-strategy-authoring.mjs`，证据目录 `.tools/verification-local/strategy-authoring/`。

| 步骤 | 结果 |
| --- | --- |
| 登录并打开策略工作台 | 通过 |
| 模板面板渲染 5 个模板 | 通过（`strategy-template-card` = 5） |
| 选择“横截面动量”后源码、名称、参数表单同步 | 通过（Monaco model 含 `class MomentumRank(Strategy)`；schema 行 = 5） |
| `self.` 触发补全并列出 `parameters` / `buy` / `order` | 通过 |
| 悬停 `buy` 显示方法签名 | 通过 |
| 表单把 `top_n` 默认值改为 3 | 通过（JSON 视图出现 `"default": 3`） |
| 插入 `import os` 触发静态校验 | 通过（`capability.*` 诊断；Monaco marker 落在第 1 行，截图 `desktop-inline-diagnostic.png`） |
| 移除违规导入后静态校验通过 | 通过 |
| 创建策略 → 保存为 v1 | 通过 |
| 参数校验 → “配置回测”可用并跳转 | 通过 |
| 刷新后版本与参数 schema 仍在 | 通过（`default: 3` 已持久化，截图 `desktop-strategy-persisted.png`） |
| 桌面 1440 / 手机 390 无横向溢出 | 通过（记录在 `report.json.viewports`） |
| 手机端模板与表单可用 | 通过（截图 `mobile-templates.png`、`mobile-schema-form.png`） |
| 控制台无错误 | 通过（`report.json.consoleErrors` 为空） |

### 真实回测（模板产物进入既有执行链路）

脚本 `scripts/verification-strategy-template-run.py`：读取上述浏览器流程创建的策略，使用其**已保存版本**的 schema 默认值（`top_n=3`，即表单编辑后的值），在数据集 `4ad3c536-681b-57d3-b2f1-7fc224093106` 上创建真实回测，由 Worker 与隔离 Runner 执行。

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

该样本为 20 只成分股、约 12 个月的单一行情区间，负收益只说明“模板可执行、结果可复核”，不构成任何策略有效性结论。