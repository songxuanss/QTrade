# 实施任务

## 1. 模板库

- [x] 1.1 新增 `apps/web/src/features/strategies/templates.json` 与类型包装，模板库从 5 个扩展到 15 个，覆盖趋势 / 反转 / 横截面 / 基准四类；验证：JSON 可被 TypeScript 导入，字段齐全，分组渲染正确。
- [x] 1.2 新增 `scripts/generate-strategy-templates.py` 作为模板唯一生成源并支持 `--check`；验证：`templates.json` 与生成器输出逐字一致。
- [x] 1.3 新增 `tests/test_strategy_templates.py`，用 `validate_strategy_source` 与 `canonical_parameter_schema` 校验全部模板；验证：6 项 pytest 通过，故意破坏一个模板时测试失败。
- [x] 1.4 在新建策略界面按「趋势 / 反转 / 横截面 / 扫地僧 / 基准」分组展示模板卡片，选择后填充源码、schema、名称与说明；验证：Playwright 逐一应用 21 个模板并各自通过服务端静态校验。
- [x] 1.5 修复多标的模板的单组标量状态 bug（`sma-cross`、`rsi-reversal`），改为逐标的字典状态；验证：20 只标的数据集上订单从 1059/成交 13 恢复为 258/172 与 42/34。
- [x] 1.6 新增「扫地僧」实战分组：龙回头、量价齐升、均线粘合突破、跳空不补、网格低吸、KDJ 金叉，并在界面标注其未经严谨学术检验；验证：6 个模板全部通过静态校验并产生真实成交。

## 2. 参数 schema 表单

- [x] 2.1 新增 `schema-editor` 纯函数模块，支持解析、序列化、增删参数与类型/范围/枚举/必填编辑；验证：Vitest 覆盖增删改与非法输入。
- [x] 2.2 工作台加入表单视图与 JSON 视图切换；验证：表单修改后 JSON 同步，非法 JSON 不被清空。

## 3. 编辑器护栏

- [x] 3.1 注册 Python 补全与悬停 provider，覆盖 `qtrade` 接口与 bar 字段；验证：Playwright 触发补全并看到条目，provider 只注册一次。
- [x] 3.2 将静态诊断映射为 Monaco markers，代码变更时清空；验证：制造 `while` 或 `open()` 违规，对应行出现标记。

## 4. 验收

- [x] 4.1 运行 Python、Vitest、ESLint、typecheck 与生产构建；验证：全部通过，宪法检查通过。
- [x] 4.2 在本地真实栈用 Playwright 走完“选模板 → 改参数 → 保存 → 刷新 → 校验 → 回测入口可用”；验证：桌面与手机宽度无横向溢出，控制台无错误。
- [x] 4.3 21 个模板全部进入真实回测链路（持久化策略 → 冻结版本 → `validated` → Worker/Runner 执行 → `backtested`）；验证：订单、成交、242 个权益点均非空，`failures` 为空。

## 验证证据

- 详见 `docs/testing/STRATEGY-AUTHORING.md`。
- 浏览器脚本 `apps/web/tests/verification-strategy-authoring.mjs`，证据 `.tools/verification-local/strategy-authoring/report.json`（21 模板逐一静态校验、桌面/手机无溢出、控制台无错误）。
- 模板回测 `scripts/verification-strategy-templates.py`，证据 `.tools/verification-local/strategy-authoring/templates-backtest.json`，21 个 run 全部 `completed`（`--only` 可只复跑指定模板）。
- 浏览器创建的模板策略单独回测 run `775fa0bd-a309-4c8c-8411-029a6135ccd4`（29 单 / 25 成交 / 242 权益点）。