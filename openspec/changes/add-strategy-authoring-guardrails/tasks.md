# 实施任务

## 1. 模板库

- [x] 1.1 新增 `apps/web/src/features/strategies/templates.json` 与类型包装，提供五个模板的源码和 schema；验证：JSON 可被 TypeScript 导入，字段齐全。
- [x] 1.2 新增 `tests/test_strategy_templates.py`，用 `validate_strategy_source` 与 `canonical_parameter_schema` 校验全部模板；验证：pytest 通过，故意破坏一个模板时测试失败。
- [x] 1.3 在新建策略界面加入模板卡片，选择后填充源码、schema、名称与说明；验证：Playwright 选择模板后三个区域同步更新。

## 2. 参数 schema 表单

- [x] 2.1 新增 `schema-editor` 纯函数模块，支持解析、序列化、增删参数与类型/范围/枚举/必填编辑；验证：Vitest 覆盖增删改与非法输入。
- [x] 2.2 工作台加入表单视图与 JSON 视图切换；验证：表单修改后 JSON 同步，非法 JSON 不被清空。

## 3. 编辑器护栏

- [x] 3.1 注册 Python 补全与悬停 provider，覆盖 `qtrade` 接口与 bar 字段；验证：Playwright 触发补全并看到条目，provider 只注册一次。
- [x] 3.2 将静态诊断映射为 Monaco markers，代码变更时清空；验证：制造 `while` 或 `open()` 违规，对应行出现标记。

## 4. 验收

- [x] 4.1 运行 Python、Vitest、ESLint、typecheck 与生产构建；验证：全部通过，宪法检查通过。
- [x] 4.2 在本地真实栈用 Playwright 走完“选模板 → 改参数 → 保存 → 刷新 → 校验 → 回测入口可用”；验证：桌面与手机宽度无横向溢出，控制台无错误。

## 验证证据

- 详见 `docs/testing/STRATEGY-AUTHORING.md`。
- 浏览器脚本 `apps/web/tests/verification-strategy-authoring.mjs`，证据 `.tools/verification-local/strategy-authoring/report.json`。
- 模板产物回测 `scripts/verification-strategy-template-run.py`，run `775fa0bd-a309-4c8c-8411-029a6135ccd4`（29 单 / 25 成交 / 242 权益点）。