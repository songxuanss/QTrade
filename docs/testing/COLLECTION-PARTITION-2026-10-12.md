# 来源感知分段验证

本次完成新计划的来源感知时间分段及可配置收紧，不代表整体专业工作台已完成。

## 实际部署

实际入口为 `http://127.0.0.1:13000/workspace/data`，复用既有认证API3122/Worker3123；采集18100自身数据库和归档。采集镜像 `sha256:b7923802ff31587ec77a331f340d9c20bc220911b69dd265746837311101a082` 已部署。旧计划与产物没有迁移或重写。

Pro 分钟按北京时间年边界，日线按年，因子按年且单段不超过365自然天；普通分钟仍采用保守七天范围，不再作为所有类别统一要求。`COLLECTOR_MAIRUI_PARTITION_MAX_DAYS=0` 不加运维上限，正整数仅收紧策略；总日期范围默认无限制。接口分页、源覆盖和质量完整性不能由分段策略本身证明。

## 证据

- `pytest tests/test_collection_planning.py tests/test_independent_collection.py`：28通过。覆盖往年及当前年边界、市场时区、半开连续范围、因子上限、配置收紧和非法配置。
- `tests/test_independent_collection_postgres.py`：隔离临时PG数据库6通过；使用真实持久存储、受控供应商fixture。覆盖幂等、来源/作用域隔离、租约fencing、重启恢复、无观测/撤权、预览冲突、重试，以及旧七天计划在配置切换后的身份/范围/完成覆盖不变。fixture不计真实来源成功。
- 首次PG运行错误地连接正在消费的部署采集库，测试新建隔离作用域任务被真实Worker领取，导致领取断言失败。随后在同PG实例创建专属临时数据库运行并删除该临时数据库，6项通过；无业务数据恢复/删除。以后持久化测试必须隔离数据库，不能与真实Worker竞争。
- 实际13000运行 `QTRADE_VERIFICATION_ORIGIN=http://127.0.0.1:13000 node tests/verification-independent-collector.mjs`：8检查通过。600519.SH 贵州茅台2025-09-01～10 Pro5m为一个子任务（此前两个），384条行情真实来源→Worker→归档→导入→图表，刷新后版本引用对应本轮产物；pe_ttm五条和双成员池96条亦通过。重复补缺、延长缺口、只读/跨域拒绝、桌面1440/手机390无溢出及axe均验证。
- `PYTHONPATH=packages/data-contracts/src:packages/data-core/src:adapters/data_http/src .venv/bin/python scripts/verification-independent-collection.py`：本轮4个非空发布产物原始归档及分页标准摘要/数量、真实行情存储匹配。
- Ruff及变更四个源码文件Mypy通过；`git diff --check`通过。唯一测试警告是现有Starlette/httpx测试客户端弃用提示。

## 仍待验证

当前年全年分钟实际完整性、完整因子一年/增量/质量/配额、财务事件扩展、持久调度、新鲜度/告警、隔离恢复及性能目标仍按整体任务台账推进；短范围成功不推导这些项完成。这里验证的是实际十天Pro请求不再被强制七天拆分及计划策略/兼容性。
