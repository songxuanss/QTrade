# 独立数据契约 v1

`qtrade-data-contracts` 可单独构建/安装，仅依赖 Pydantic，不导入 QTrade 身份、策略、
回测、交易、Worker、数据库或来源 SDK。JSON Schema 2020-12 位于 `schemas/v1`；
跨字段语义仍必须经过同样的契约验证。

消息显式包含整数 `schema_version: 1` 和稳定 `scope_id` UUID；拒绝未知字段/版本。
对象不可变，成员用 tuple；时间必须带偏移，输出规范为 UTC，区间为 `[start,end)`。
价格、数量、市值为普通十进制字符串，拒绝浮点、指数文本、非有限值、超过 38 位
有效数字或小数位超过 18 的输入。价格币种 CNY，成交量为标准股单位；来源适配器
负责来源单位转换。市值包含来源口径及估值时间，不能混排。

股票保持 `601288.SH` 等原标识；新指数采用 `index:` 命名空间，避免数字代码冲突。
QTrade 接入适配器将 workspace_id 映射到 scope_id，迁移必须保留原 UUID。
本包不授予权限：入口和存储必须独立鉴权，actor_id 不是凭证。

UniverseContent 验证成员内容，`UniverseSnapshot.freeze` 产生校验和；接收方核对
哈希，修改成员必须创建新快照。规范哈希包括所有字段，唯独不含 snapshot_id 和
content_sha256；成员顺序有语义。当前快照不得带历史有效区间，历史成员必须提供
有效区间；实际历史来源证据另由目录实现验证。

CollectionSelection 冻结选择；CampaignPlan 检查范围、去重和逐股七天分段数。
CampaignProgress 的整体完成要求所有计划工作采集完成且所有成员有非空可查看
发布；队列接受、部分展开或原始 Tick 完成不能被解释为全部完成。

CatalogQuery 的 cursor 是有界不透明文本，不是权限。目录实现必须将游标绑定
scope、标的、周期、范围、固定数据集及排序，每页重新鉴权，换条件拒绝旧游标。
BarPage 保留作用域、数据集和原始批次引用；PublicationManifest 只描述非空发布。
manifest 哈希由发布实现实际核对，不以格式合法代替内容核对。

错误只输出稳定分类、关联 UUID 和可选等待秒数，不输出上游 URL、例外文本或密钥。
来源能力的 verified 仅代表实测能力，配置开关不等于供应商授权。

本包是契约基础，不表示独立采集服务或新增 UI 已完成。后续 Adapter、授权、
持久化、Worker 和迁移验证由 OpenSpec 分别追踪。

```bash
PYTHONPATH=packages/data-contracts/src .venv/bin/python scripts/export-data-contracts.py
.tools/uv/bin/uv build --wheel --out-dir .tools/data-contracts-dist packages/data-contracts
.venv/bin/pytest tests/test_data_contracts.py tests/test_architecture.py
```
