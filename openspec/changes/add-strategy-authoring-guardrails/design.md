# 设计

## 1. 上下文

策略版本由源码、参数 schema 与依赖清单冻结；Web 负责编辑，API 负责静态校验与保存，Runner 负责执行。本次只改编辑体验，不触碰执行链路。

## 2. 决策

### 2.1 模板以 JSON 作为唯一数据源

模板放在 `apps/web/src/features/strategies/templates.json`，TypeScript 只做类型包装。原因是同一份模板必须被两个运行时消费：

- Web 读取并填充编辑器；
- Python 测试读取同一文件，用 `validate_strategy_source` 与 `canonical_parameter_schema` 校验。

若把模板写成 TS 字符串，Python 侧只能重复维护或正则解析，容易漂移。JSON 让两侧读同一事实。

### 2.2 模板必须自带可发布的 schema

每个模板声明 `id`、`name`、`summary`、`tags`、`code`、`schema`。`schema` 必须通过 `canonical_parameter_schema` 的受限子集（object、properties、白名单类型、数值范围、enum），因此模板参数天然可以渲染到回测表单。

### 2.3 参数 schema 表单与 JSON 双向

表单编辑在内存中维护规范化的 schema 对象，序列化后才写入版本。切换 JSON 视图时直接展示序列化结果；从 JSON 切回表单前先解析，解析失败保留 JSON 文本并显示错误，不清空用户输入。未知键在表单视图中只读展示，避免静默丢弃。

### 2.4 诊断内联显示

静态校验返回 `code`、`message`、`line`、`column`。使用 `monaco.editor.setModelMarkers` 写入当前 model，severity 统一为 error（服务端只返回 error）。每次校验前先清空 markers；代码变更时同步清空，避免陈旧标记。

### 2.5 补全与悬停是静态知识，不引入语言服务器

Monaco 的 Python 支持不含语义服务。本次注册：

- `registerCompletionItemProvider("python")`：提供 `qtrade` 导入、`Strategy` 类骨架、生命周期方法、下单方法与 `bar` 字段；
- `registerHoverProvider("python")`：对已知标识符返回签名与约束说明。

provider 在模块级只注册一次，避免组件重挂载重复注册。补全只做静态提示，不作为校验依据；真正的拒绝仍由服务端静态校验与 Runner 完成。

### 2.6 模板不进入 manifest

模板只影响初始源码。版本冻结后，`code_sha256` 已覆盖实际执行内容，因此不新增 `template_id` 字段，也不改 manifest 契约。模板来源可在描述中自行记录，不作为复现依据。

## 3. 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 模板源码与校验规则漂移 | Python 测试读取同一 JSON，逐个跑静态校验与 schema 规范化 |
| Monaco provider 重复注册 | 模块级 `registered` 标志，只注册一次 |
| 内联 marker 残留 | 代码变更与重新校验前清空 |
| JSON/表单切换丢失未知字段 | 未知键保留并只读展示；解析失败不覆盖文本 |
| 模板暗示收益 | 模板摘要只描述规则与适用场景，不写收益承诺 |

## 4. 验证

- Python：`tests/test_strategy_templates.py` 校验模板数量、唯一 id、schema 合法、源码通过静态校验。
- Vitest：模板结构与 schema 编辑器的增删改、序列化、错误分支。
- Playwright：新建策略时选择模板 → 代码与参数表单填充 → 保存 → 刷新后版本仍在；内联诊断在校验失败时出现。
- 宪法：模板仍通过既有 Runner 执行，不新增 `exec` 调用点；不引入第三方依赖。

## 5. 回滚

删除模板库与 schema 表单即可回到手写 JSON；已保存版本不受影响，因为版本只冻结源码与 schema，不依赖模板元数据。