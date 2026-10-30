# 研究HTTP部分响应证据

承接[完整响应留存](RESEARCH-HTTP-RECEIPTS-2026-10-16.md)，补齐总超时、截断和正文超限时临时字节被清理的问题。仍不宣称完整4.1/4.4或Goal已完成，整体18/42不变。

## 设计和review说明

- BoundedHttpClient收到响应头后原子保存安全状态，正文无用户态缓冲按解压字节写入私有临时文件。超限只保留配置上限的前缀并失败，不返回截断成功。默认正文上限保持64,000,000字节、原总期限及用量租约规则不变。
- 父进程必须在kill/join或确认子进程终态后读取文件，避免仍在写入时声称证据稳定。TransportFailure的args/repr仅固定错误码，正文和状态通过私有使用的属性交给archive-first回调，不进入日志、URL、公开正文接口或异常文本。
- 研究回执使用既有v2契约的complete=false；正文先不可变写入并fsync，再记0020索引，随后抛传输错误。收到响应头但未收到正文可为零字节；未收到任何响应不制造回执。HTTP200和零/非零字节都不证明解析、质量或发布成功。
- capture写入失败同样终止，不掩盖为供应商成功；已发生的请求在原usage finally完成失败记账，不重复预留或返还信用。超限字节数表示保留前缀，不声称全部网络接收量。共享行情/因子传输回调也能保留既有v1安全envelope，HTTP证据仍不算有效RAW行或覆盖。
- 正常返回和失败均清理临时文件，部分字节以私有归档继续保存。强杀Worker/宿主不能保证回调执行或durable归档，可能仅剩临时私有目录；这不构成离线重处理就绪。代码注释说明这些时序、幂等/失败和进程所有权约束，无新迁移或冻结Schema修改。

## 验证命令与结果

- `.venv/bin/pytest tests/test_bounded_supplier_http.py`：12项通过。真实loopback TCP成功、延迟、持续小块慢传输总期限、正文超限、截断、只有头、响应前断开、Worker死亡清理和非法期限。验证部分前缀内容/长度、固定异常repr、无遗留socket owner；不是供应商故障证明。
- `.venv/bin/python scripts/selftest-research-acquisition.py`：59项通过，六个独立自有临时PG库，19+11+7+5+12+5。新增五种真实TCP故障证明私有正文/SHA与数据库回执可重读，未生成解析RAW、标准产物或发布。连接失败无回执，空正文不误判无观测成功；原研究队列/预算/转换拒绝回归保持。
- `.venv/bin/pytest tests/test_usage_execution.py tests/test_mairui_history_bars.py tests/test_mairui_ticks.py tests/test_mairui_cache_concurrency.py tests/test_mairui_research.py tests/test_mairui_distributions.py`：36项通过。部分失败及回执写失败都保留一次失败记账和字节数，日志不含fixture私有正文或key。首次命令误写不存在的test_mairui_collection.py导致未收集，使用实际套件路径后执行通过。
- `.venv/bin/pytest tests/test_independent_collection.py tests/test_collection_quality.py`：51项通过，含共享v1归档完整性、未知行数和未发布回归。新增测试首次漏传source_captures的before参数，修正为实际读取时刻后原断言通过；非产品放行变更。
- Ruff、三个修改生产模块Mypy、验收脚本ESLint/Prettier及OpenSpec严格校验通过。部署collector三个修改模块逐字节SHA与工作树一致。

## 部署及实际主链

原13000 Web→3122会话API→18100独立collector，原身份、PG和归档保留。collector `sha256:0d01e28f5d3430d30ecb5c1b70611d7f1a6ec223dac252b8b99dc74f9ee42018`；Web `sha256:0b29ef0bdaa398c3a09d0a38b0a46f690e6228b48764b8d46fb76c7961cbe2c3`。无API契约/业务API代码变化，不借其他进程重启掩盖故障。

在专用验证作用域重新执行financial-ui与distribution-ui脚本，均由实际SSO/UI创建新任务，经API→Worker→真实MaiRui响应→非空RAW/标准→质量→发布→精确版本浏览/刷新，通过财务income2015的6个披露版本及31条事件，桌面手机axe0/无溢出。新任务身份、版本和数量位于私有review结果，不借上一轮产物。

`verification-research-response-fixture.py --partial`只准备两个终态受控fixture：financial200不完整前缀/timeout，corporate200仅响应头/空正文/unavailable。通过受控TransportFailure覆盖生产_get回调及真实持久化，无供应商请求、不投queued任务、不计Worker重试或UI创建证明；与上述真实成功分开。

`verification-research-response-archive.py --partial`通过：按两次新真实UI任务验证完整正文SHA/长度、解码原值与全部RAW批次逐项相同、标准/发布引用；两受控任务失败/无发布，正文包含合法零字节、complete=false且SHA一致，0020实际账本校验和不变。

`QTRADE_RESPONSE_PARTIAL=1 node tests/verification-research-response-ui.mjs`通过：实际SSO管理员1440桌面与采集READ risk_manager390手机，分别读取两项受控部分响应及两项新真实发布。校验零字节和complete=false、安全元数据及SHA、不完整提示、失败与已发布区分、刷新重开同ID、受控读取中止后旧参考/按钮释放/恢复、非法参数/跨域/匿名/正文端点拒绝、键盘/Escape、axe0和无溢出。真实成功完整响应不被误标不完整。桌面/手机模态截图已人工查看；私有review结果保留，不提交身份凭证或正文。

受控部分响应各只有一条，此轮不重新证明多页；20+1分页由前一阶段完整回执验收证明。新增fixture采用受控异常，不替代隔离TCP进程/存储测试或真实供应商集成。验收在北京时间10月16日深夜完成，交付记录于17日整理。

## 仍待完成

异常分页的原始证据、全部类别/规则与检查状态、有界离线重处理、修复后新版本及旧引用完整隔离仍需推进，4.1/4.4保持未勾选。不得从此阶段推导财务PIT、全历史完整性、新鲜度、回测或实盘就绪。
