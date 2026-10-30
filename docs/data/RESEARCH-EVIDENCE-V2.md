# 研究证据存储 v2

本文说明研究数据的原始/标准归档、不可变清单及独立 PostgreSQL 元数据。普通历史财务与有限分红送转已有真实采集/发布/UI证据；受控扩展类型、存储或HTTP成功本身仍不能证明供应商接入、PIT或研究就绪。

## Module与Interface

核心 `research_evidence.py` 声明 ArchivePort 和 EvidenceRepository seam，FileResearchArchive 和 PostgresResearchEvidence 为可替换Adapter。核心不导入文件/PG或QTrade业务代码，采集只使用自己的库与归档。调用流程为 register冻结计划 → capture原始页 → append_raw元数据 → normalize → standardize → append_artifact；质量及发布由后续生命周期处理。

FileResearchArchive 使用单独 research-v2/作用域/UUID目录，原始Parquet和标准Parquet分别保存。批次清单绑定作用域、请求/计划、来源/类别/标的、半开日期范围、分页游标、采集时间、原始字节和内容摘要/数量。标准清单保存映射和能力快照、完整原始批次血缘、标准字节/内容/语义摘要与数量。公开标准记录使用明确五种类型，不用任意JSON或行情字段；任意来源字段仅保留在raw证据。

写入通过临时文件flush/fsync和硬链接创建不可变目标，已有不同内容拒绝。恢复重验字节/内容/数量和血缘；清单损坏或身份被替换不能恢复为有效数据。原始空页可以留证据，但不能据此产生非空标准成功或计价格覆盖。转换拒绝保留原始，修复后的标准内容可生成另一产物，旧产物不改写。

`semantic_sha256` 根据标准记录内容版本排序计算，采集时刻改变可产生新归档证据，但同语义不当作数据修订；指标、披露日期或定义/证据变化会改变语义版本。完整标准记录摘要仍包含原始顺序和采集时间，用于精确冻结引用。

## 持久化与状态

新增迁移0004只增加 research_run/research_raw_batch/research_artifact/research_artifact_raw 与防改写触发器，不修改已锁定0001～0003。冻结计划按作用域/请求幂等，原始批次及标准元数据append-only，数据库拒绝UPDATE/DELETE证据；相同ID不同清单冲突。标准记录必须先有已持久原始血缘，不接受未保存或跨作用域来源批次。

| 当前证据状态 | 意义 | 不能推导 |
| --- | --- | --- |
| registered | 冻结计划已登记 | 不代表已排队或来源调用成功 |
| raw_persisted | 原始页及元数据已留存，包括空页 | 不代表标准化通过、完整覆盖或已完成 |
| standard_persisted | 标准产物及血缘已持久 | 不代表质量通过、已发布或终端可见 |

检查通过/失败、发布及消费者可见由独立质量/发布记录表达，不将标准存储直接当公开发布。实际普通历史财务及有限事件通过版本化发布查询消费，其余类型的真实能力以来源目录和固定验收范围为准。

## 解码前的HTTP响应回执

财务/事件适配器实现同步`fetch_with_capture`，由ResearchAcquisitionEvidence管理一次fetch的独立捕获窗口。先将完整收到的解压后HTTP字节写入私有对象并fsync，再追加0020元数据，之后才能判断状态/解码JSON/生成原始页。每次fetch有新的acquisition_id，最多10份响应，窗口关闭后保留回调不可使用；缺少回执、存储失败或被适配器吞掉的写入失败均不能放行有效页。普通非HTTP受控适配器仍可使用原有RAW页接口，不据此声称它们具有HTTP证据。

ResearchResponseReceipt/Page是独立新增v2契约，不向既有计划/原始页/发布模型添加默认字段。回执绑定作用域、请求、原操作主体、计划、来源/类别/映射、标的、分片、游标、捕获身份及索引、HTTP状态、完整标志、字节数、SHA和记录时间；不包含正文、URL、头、异常字符串或标准记录数量。记录时间不是历史可知时间，HTTP 200及正文完整不等于数据有效、完整覆盖或发布成功。

私有对象位于研究归档的scope/request/responses下，正文与清单使用不可变写入。元数据单独入库，scope/request外键、载荷摘要和不可变触发器保护；没有伪造旧任务回执或回填旧HTTP状态。解码失败时research_run仍可处于registered而已有HTTP回执；这不改变raw_persisted所要求的解析原始页。

公开查询为collector GET `/v2/research/jobs/{request_id}/response-receipts?limit=20&after_id=...`，对应QTrade会话桥接`/data/collector/research/jobs/{request_id}/response-receipts`。当前READ及精确任务/计划绑定，no-store；limit1–100，按captured_at/receipt_id降序，游标必须属于同任务。跨页不保证冻结快照，可刷新重读。API仅返回已记录元数据，不重读正文或证明当前对象完整性；私有内部response_body读取须校验字节数和SHA，浏览器无正文端点。

数据库连接5秒、语句10秒、锁5秒。回执/索引失败阻止后续转换与发布，已收到私有字节仍可保留供恢复调查；不得因HTTP响应已归档就把失败任务标为完成。研究来源发生总超时、截断或正文超限时，回收连接进程后将已收到的有界解压前缀保留为complete=false回执；只收到头可为零字节，没有收到响应不编造回执。该前缀不进入RAW解析、转换或发布，字节数不是超限请求的全部网络流量；强杀Worker/宿主不保证归档回调执行。异常分页及完整离线重处理仍按原Goal保留待完成。

## 验证与操作

```sh
.venv/bin/pytest tests/test_research_evidence.py tests/test_research_interface.py tests/test_research_contracts.py tests/test_independent_collection.py
.venv/bin/python scripts/verification-research-persistence.py
```

第二条读取Git忽略的独立采集配置，在既有PG实例创建本次随机生成的 research_test_* 专属数据库，运行研究持久化与旧采集存储测试后仅删除该临时数据库。不会对生产业务库删除/恢复，也不会让真实Worker消费测试任务。测试数据库参数禁止使用collector主库。

上述初始存储验证均使用fixture，覆盖原始→转换拒绝→重启留存、空页、五类标准往返、摘要/清单拒绝、修订隔离、并发/血缘/作用域和旧v1兼容；不能单独证明真实源。真实财务/事件、发布/UI及后续回执证据另见docs/testing报告。新增回执使用`scripts/selftest-research-acquisition.py`的随机隔离库验证；只删除自建临时库，不清理业务数据。
