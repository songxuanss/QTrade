# 研究接口阶段证据

2026-10-12。新增v2来源发现、纯预览、明确版本分页查询及Provider/ReadPort seam。**真实财务/事件接入、持久质量发布和完整扩展生命周期仍未完成**。

## 受控契约

`pytest tests/test_research_interface.py tests/test_research_contracts.py tests/test_independent_collection.py`：67通过，含新增接口17个参数化实例。调用真实collector路由与当前权限代码、受控财务Adapter和Reader；不使用供应商真实数据，不计live接入。覆盖来源发现、预览无获取副作用、计划摘要、权限/跨域/历史边界/类别/日期轴、未配置入口、缺段重叠及额外标的、Provider游标获取、标准明确类型、版本/分页/摘要、未知可知时间和未来版本拒绝。

Ruff和四个变更源码Mypy通过。已有Starlette/httpx测试客户端弃用警告保留，无产品故障结论。

## 实际部署

采集18100镜像 `sha256:98cfc054b189aeb41c00a0c62c335f3f2ffde888b205a7d7a925ff0debb89909` 已部署，既有13000 Web/3122认证API/3123Worker不替换数据。

`.venv/bin/python scripts/verification-research-interface.py`：6项通过。实际服务身份/授权回查链下，无身份401、目录无虚构已接通来源、未接通财务预览明确unsupported、版本3请求422、跨作用域body403、不存在研究产物404、旧v1完成非空产物保持可读。此阶段v2目录为空，后续来源接入会增加带证据能力声明；不能由空目录声称来源不提供这些数据。

`PYTHONPATH=packages/data-contracts/src:packages/data-core/src:adapters/data_http/src .venv/bin/python scripts/verification-independent-collection.py`：重部署后4个既有非空产物的原始归档/分页标准摘要与数量、行情存储核验通过。不是新增v2真实采集闭环。

## 剩余工作

任务2.4～2.6补持久化/不可变归档、门禁/发布/版本/as-of查询及完整受控生命周期；3.3/3.4完成三表及有限分红真实供应商接入，6.4完成UI消费刷新。接口、fixture或健康不能替代这些验收，整体目标保持未完成。
