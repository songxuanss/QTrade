# 行情/因子转换前响应留存阶段

对应 `complete-professional-data-workbench` 4.1 的基础改造；版本化质量运行、状态/API、完整门禁和重处理仍未完成，任务保持未勾选。

原流程 `provider.fetch` 完成转换后才存 v1 原始 Parquet，坏时间或数值会在归档前失败。现在核心传入明确 raw-capture 回调：供应商成功解码响应→不可变快照→结构/时间/数值转换→既有发布。普通历史、Pro 年片和因子历史均在 `_rows` 或时间过滤前调用；来源日历首次获取也在校验前留存。有效年片缓存以 `cached_history` 单独标识，归档失败不删除有效缓存或额外请求供应商。

新增严格 `CollectionRawCapture` 收据，包含作用域、任务/子任务、主体、来源、转换版本、租约/尝试、类别、内容 SHA、采集时间及可判定响应条数。文件存于已有子任务目录的 `source-captures/`，JSON 内容按摘要命名、收据按确定性身份命名，沿用 hardlink、fsync、0600、不可变冲突检查。同租约同输入重复留存保持首个收据；血缘改变拒绝。保留既有 v1 manifest 字节、公开发布字段及冻结消费者引用。

此处响应是已成功 JSON 解码的供应商内容，Decimal 使用文本保持精度；不是未经解码的 HTTP 字节包。错误 HTTP 状态、无法解码/超限响应及因子目录的转换前输入尚未纳入此新增回调，不能宣称所有传输异常都已归档。复用的交易日历仍是已验证内存缓存，不为每次任务伪造新的来源响应。

收据不是发布，不产生价格覆盖。空因子响应记录 response_rows=0；非法结构条数 unknown，不能把封装对象计成一条有效因子。坏结构归类 unverified，不当作网络不可用自动重试。转换错误的子任务仍保存失败码，未写 manifest、消费者读不到产物。

受控测试证明：Worker 实际调用适配器并在坏时间标签拒绝前落下历史响应；坏因子结构及空响应落下原内容且无发布；相同收据幂等，血缘变更冲突。36 项独立适配/默认/桥接测试、30 项隔离 PG 研究/旧行情恢复测试通过。故障来源是 HTTP fixture，不能计为真实供应商返回坏数据的验收。

已部署 collector 镜像 `sha256:f0aa55dcb3af9c3010fb7a64797245b067b38101696bc2e02b2a2acdde4ac2e3`。新增可选验证 `QTRADE_VERIFY_RAW_CAPTURES=true ... scripts/verification-independent-collection.py`，在原归档/公开分页检查之外核验真实非空发布关联收据、原内容 SHA、作用域、来源和转换版本，只输出脱敏摘要，不输出供应商内容或配置。

部署证据：13000 既有 UI 主链 8 项通过（Pro5m 384、因子 5、股票池 96）；Pro 本轮复用已验证年缓存，以 cached_history 记录，不声称本轮冷下载年片。实际一年日线 UI 自动默认→两段 HTTP 历史响应→242 条→农业银行图表→刷新及拒绝/桌面手机，共 7 项通过；运行 `fcb8fc96-5752-4db4-ac30-cd778ec9bd79`，发布 `e86781cb-c82f-51e4-8380-0537fb7ac403`、`cfd0309a-eed8-5bc4-b6c0-0839ce4dbc03`。北向历史空响应 UI→Worker→no_data/无发布→刷新通过，运行 `75defcae-4889-4796-a3ae-d751aff75f0a`。

归档验证对原 4 个非空发布核对标准分页/原始 Parquet/消费者一致性；额外通过 `QTRADE_VERIFY_CAPTURE_JOB_IDS` 指定上述一年和空响应运行，合计 7 子任务、8 收据核验 SHA/作用域/来源/转换版本；年度两段为 history，空因子为 factor_history/response_rows=0 且无 manifest。报告将目标标为 part_id 和 published 布尔值，不能将无观测子任务 ID 误叫发布 ID。私有脱敏报告 `independent-collector-persistence.json`、`annual-daily-ui-results.json`、`factor-empty-ui-results.json` 保留。无新质量门禁通过的泛化结论。

剩余交付：传输层异常响应安全留存、原始证据持久索引和公开状态、类别版本化门禁、完整阻断/修复/重处理 UI、重试及取消阶段语义。当前数据已有基础转换验证，不能因此声称完整专业质量门禁已交付。
