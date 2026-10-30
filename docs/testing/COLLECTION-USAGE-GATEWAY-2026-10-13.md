# 请求用量网关阶段验证

当前目标仍为全部工作台要求；OpenSpec `complete-professional-data-workbench` 为13/42，5.1–5.4未完成。本阶段接入日信用执行门禁，不等于完整用量、物理连接并发或公平调度交付。

## 实现与review约束

生产collector向MaiRuiCollection注入供应商无关UsageGate。供应商Adapter仅传安全operation和内部信用，账本不取得URL/证书。ContextVar携带已鉴权actor/scope，HTTP异步依赖、线程池、预览/提交及Worker/研究执行结束后复原；缺采集作用域在出站前拒绝。元数据后台可只扣来源预算。

调用前reserve原子扣账，因子历史5信用，其他当前操作1信用。失败不退款；最终结果无法持久化时失败关闭，不能继续消费响应或日志误记成功。耗时不含pacing/预留等待，响应字节为HTTP解码后content长度。默认日信用来源10000/作用域2000，预留并发来源4/作用域2，租约3600秒；均为内部防护，不能作为供应商收费或权限依据。

## 可复核证据

- `pytest tests/test_usage_execution.py tests/test_collector_http.py tests/test_independent_collection.py tests/test_usage_policy.py -q --tb=short`：48通过。覆盖超预算/缺scope出站零调用、调用前信用可见、HTTP403/429/500及timeout保留费用、安全结果、并发线程作用域隔离、HTTP依赖上下文、最终保存失败关闭及意外transport失败不记成功。HTTP故障为MockTransport受控证据。
- `scripts/verification-research-persistence.py`：39通过，使用隔离真实PG，包含8项用量/竞争/网关测试。新网关测试使用真实账本及受控HTTP，不计真实供应商接入。
- Ruff通过，Mypy六个变更模块通过。
- 本轮13000真实UI采集回归：5m单股384、所选历史因子5、多成员96；共8项含刷新、手机、只读与作用域拒绝。`verification-independent-collection.py`额外复核4个非空发布的公开分页、原始/标准摘要、原始捕获、质量和市场消费者。
- 财务UI桌面/手机6个披露版本、事件UI桌面/手机31事件，均刷新可见，axe0。证据文件为本机私有review目录的本轮financial/distribution/independent结果；这些证明主链结果，不以脚本名称或健康检查代替。
- 当前真实PG `qtrade_data.usage_request`统计：1次factors.history（5信用，298字节），1次metadata.calendar（1信用，2675字节），1次metadata.names（1信用，290158字节），2次research.data（2信用，9494字节），全部finished且有scope。数字为本轮查询快照，后续运行可增长。
- 行情回归使用既有供应商年度缓存，没有新增bars.history扣账；缓存和不可变新产物均有证据，不将缓存读取声称为本轮新行情RPC。

最终故障日志修复重建后再跑同三条UI脚本全部通过。新部署镜像为 `sha256:92c85cbb85ab66505e2c2db8ab92b5fb90725c853e557d92a4108caae63eea27`。这次Worker重启后发生2次真实bars.history（2信用、2705761字节）；另有后台factor目录1次（1信用、11932字节）。最终账本快照共13次finished请求：bars2、factor历史2、calendar2、factor目录1、names2、research4。初轮缓存读取与最终重启新HTTP请求分别记录，不能混为一次证据。供应商无权/超额路径仍为受控测试，没有故意耗尽用户真实日预算。

## 尚未通过的要求

整体HTTP截止时间、取消/租约维护及物理连接并发、拒绝事件持久化/公开用量API/UI、单次规模及确认阈值、公平领取、未来定时/复核和旧股票池独立出站路径仍待实施验证。来源额度不代表账户真实配额，存储字节/重试/队列等待还未形成完整用量视图。未新增付费来源或实盘调用，未修改旧迁移/旧发布版本。
