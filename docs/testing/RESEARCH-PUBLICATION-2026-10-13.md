# 研究发布门禁阶段验证（2026-10-13）

本记录对应 OpenSpec 2.5，不是整体工作台验收。研究 Adapter、Worker 全生命周期和实际财务/事件 UI 消费仍待完成；受控 PIT 不计 MaiRui 实接。

## 执行证据

- `pytest -q tests/test_research_quality.py tests/test_research_evidence.py tests/test_research_interface.py tests/test_research_contracts.py tests/test_independent_collection.py`：85 项通过。
- `python scripts/verification-research-persistence.py`：17 项通过；真实 PostgreSQL 随机隔离库，使用受控记录，测试后仅清理自身生成数据库。覆盖阻断→修复→发布、幂等、旧引用保持、按时点修订隔离、未知可知时间排除、普通历史拒绝 as-of、角色/作用域及实际 HTTP 查询。
- `python scripts/verification-research-interface.py`：实际18100接口7项通过；未连接类别不提供虚构可用入口，研究目录仍为空，未知发布404。
- `PYTHONPATH=packages/data-contracts/src:packages/data-core/src:adapters/data_http/src python scripts/verification-independent-collection.py`：既有4个非空发布的原始/标准归档、分页摘要与消费导入复核通过。未新建本阶段真实供应商下载，不计新增数据闭环。

部署镜像：`sha256:69244743130fd18fcf0bc83648c2c8cc7712ee16f4b65f9d47e6948b97e984b9`。实际数据库迁移账本与本地文件SHA256相符：

| 迁移 | SHA256 |
|---|---|
|0005_research_publications.sql|5df97d96b82b8e4002553df52105f487ffa20c9a1a472ddef3f94f3c29ba5636|
|0006_research_gate_integrity.sql|cbfce5a3d7e73b7f93c1c7879b4f643052635eba896b020e89f5283182b49210|

## 修复与边界

统一发布时间规范JSON格式，避免UTC字符串表示影响清单摘要。追加0006拒绝缺字段/JSON null绕过门禁，不修改已应用0005。SQL直写伪造空质量对象的拒绝由隔离数据库测试证明。

研究查询仅消费已发布清单，要求明确内容版本；游标绑定作用域/版本/类别/as-of。普通历史不能因披露日期而启用PIT；未知可知时刻不补造，受控修订查询验证当时可知版本。旧v1行情/因子的完整质量门禁、研究来源实接、桌面手机UI、调度、新鲜度、用量、性能及备份恢复尚未验收。本阶段未新增研究UI，不能以API测试宣称UI通过。

测试有一项现有Starlette/httpx弃用警告，无测试失败。总体目标保持未完成。
