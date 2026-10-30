# 公开契约 v1

`src/qtrade_contracts` 是 Python 验证实现；`schemas/v1/*.schema.json` 是语言无关的
JSON Schema 2020-12 契约，供 BFF、前端与外部 Adapter 使用。

所有消息显式包含 `schema_version: 1` 和 `workspace_id`，未知字段与版本被拒绝。
ID 使用 UUID；Instrument 使用显式稳定 ID 并引用规格版本；事件时间戳使用带时区的 RFC 3339。
区间统一为 `[start, end)`。价格、数量、金额以普通十进制字符串传输，拒绝浮点输入、
NaN、Infinity 与指数形式，防止 JavaScript 精度损失。数量单位是 Instrument 单位，
价格/资金按声明货币，时间戳表示 bar 开始时点。tick 行情不使用 Bar；由 provider 专有
事件经独立 Trade/Book 契约转换，未来扩展不改变 Bar 的时间语义。

不可变对象采用 frozen 模型和 tuple；参数和参数 schema 使用经过排序、无重复键的
规范 JSON 字符串，避免嵌套字典绕过不可变性。Manifest 参数哈希必须匹配规范 JSON。
验证器进一步检查 OHLC、订单价格类型、风控允许结果、Workspace、意图与追踪关联；
JSON Schema 不能表达所有跨字段规则，其他语言实现必须通过同样的语义契约测试。

每个字段含义由对应领域上下文拥有；契约不含 ORM 或引擎对象。破坏性修改引入新版本目录，
保留旧版本解析；v1 变更必须通过导出一致性、示例 round-trip 与非法输入测试。

重新导出：

```bash
PYTHONPATH=packages/contracts/src .tools/uv/bin/uv run python scripts/export-contracts.py
```

CalendarSchedule 的时段边界使用不带偏移的本地时钟，配合显式 IANA 时区、交易日期
与 DST fold 解析为 UTC；不能将本地时钟当作事件时间戳。日期覆盖与闭市覆盖显式声明，
覆盖范围外返回 unavailable。日历版本不可覆盖，历史查询可指定固定版本。
