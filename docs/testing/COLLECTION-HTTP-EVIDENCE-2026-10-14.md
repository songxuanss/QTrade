# 完整HTTP响应留存与发布隔离

整体14/42，4.1保持未完成。本阶段修复行情/因子HTTP状态错误或损坏JSON在解码归档前丢失响应的问题，不声明完整传输失败/转换检查/重处理已完成。

## 机制

日历、普通/Pro历史、因子历史将原RawCapture回调传入_get。完整HTTP解码后字节在状态/JSON分类前保存http_response私有回执和base64正文，附状态、安全固定operation、complete=true，不添加URL/头/凭证。状态错误继续按原分类失败、信用不退；归档失败关闭。安全分类与信用使用同一纯函数，避免诊断与计费不一致。

HTTP回执不等于解码观测，不满足门禁raw_missing、不计成功覆盖、不进入发布raw/standard；正常history/cached_history/factor_history证据仍必须存在。缓存只记录cached_history，不伪造HTTP请求。回执绑定scope/part/job/actor/attempt/lease和不可变摘要，目录700/文件600；base64不是加密。原响应可能包含供应商诊断信息，禁止复制到日志/截图/报告/提交。旧发布、质量规则v1及manifest/schema均不改，未新增迁移。

## 测试与部署证据

- `pytest tests/test_independent_collection.py tests/test_collection_quality.py tests/test_usage_execution.py tests/test_mairui_cache_concurrency.py tests/test_factor_catalog_cache.py -o addopts='' -q --tb=short`：74通过。完整401/429/503、损坏JSON在异常后仍有精确字节、未知response_rows而非伪造零；无发布及安全日志无正文。并发年片仅一笔HTTP，第一调用HTTP+history、第二cached_history。原缓存测试预期首回执history失败后更新为新明确顺序，仍保留一次请求/同内容断言。
- `scripts/verification-research-persistence.py`：68项隔离PG回归通过。真实Worker调用受控HTTP，经真实私有归档保存错误响应，publication为空、公开publication查询404、消费者待投递中无该空间、无delivery。只有HTTP回执但返回标准记录的受控适配器被raw_missing阻断，不产生成功覆盖。供应商故障是MockTransport，不能写成真实来源权限/限流事件。
- 稳定部署13000实际SSO/UI→3122认证API→18100独立Worker/已授权来源/原卷，8项主链通过：5m384、所选因子5、两成员池96及重复/补缺/权限/桌面手机刷新。行情可复用真实年度缓存；不把缓存当新增行情RPC。
- 开启raw capture/quality校验的公开持久验证：4个非空发布raw/standard分页摘要及市场消费者一致，私有回执内容哈希及新HTTP封装字段/base64校验通过。不把HTTP封装传给市场消费者。
- 质量UI只读7项回归通过：passed/warning/no_observations/legacy_unchecked、API及刷新、跨job拒绝、1440/390布局和axe。它读取已有真实质量状态，不代替错误HTTP或阻断异常的部署UI验收。
- Ruff/Mypy三模块与OpenSpec严格验证通过。collector镜像 `sha256:eab59dce37388e3c6a2c2fa80b45974ca125ba7bee70ba23e70f724e3fc88321`；Web/API无业务变更，旧冻结publication schema逐字节不变。

## 剩余范围

研究HTTP失败、部分超时正文、目录元数据全部路径、转换失败的版本化检查与部署异常UI、完整类别门禁及有界重处理继续未完成。新HTTP字节与解码JSON并存增加磁盘，存储/性能报告须计入，不能仅统计标准Parquet。此阶段不修改确认预算、来源权益、已有数据或六主业务未就绪状态。
