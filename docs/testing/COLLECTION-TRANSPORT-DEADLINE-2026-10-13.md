# 请求整体截止与连接所有权验证

本轮实施OpenSpec 5.1/5.3的一部分，整体仍13/42。用量公开查询、UI、公平调度、定时及复核等完整要求未完成，不降低原Goal验收标准。

## 设计与配置

[HTTPX官方超时说明](https://www.python-httpx.org/advanced/timeouts/)区分连接、读、写及连接池等待；read限制等待下一块数据，不是整个响应的截止时间。持续滴流可能一直不触发read超时，因此不将HTTPX逐阶段超时作为租约所有权保证。

`BoundedHttpClient`把生产真实GET放在可终止、可回收的Linux子进程。总截止从spawn前计，包括启动及全部响应；超时kill/join后才返回给扣账完成路径。Worker死亡通过Linux父死亡信号关闭子进程，安装信号后回查PID避免安装竞态。凭证走IPC而非命令行/文件，子进程输出关闭，错误只返回固定安全码。模块文档解释该取舍，供应商路径仍只在Adapter中。

默认 `COLLECTOR_REQUEST_TIMEOUT_SECONDS=90`，正数且至多300秒。出站前从当前3600秒预留有效期减10秒清理余量，剩余不足拒绝。解码后response流限制64MB，超量关闭；每次建立独立连接，放弃连接池复用来保证清晰的请求所有权。

临时响应目录私有、body/result文件0600，正常与超时返回清除；父进程/宿主机突死可能留下私有临时目录，不将它计作不可变原始归档。失败字节为已写出的解码响应部分，不表示TCP线上精确流量或未成块的缓冲内容。HTTP完整响应到达才返回给既有原始留存及转换。

## 验证证据

- `pytest tests/test_bounded_supplier_http.py tests/test_usage_execution.py tests/test_collector_http.py tests/test_independent_collection.py tests/test_usage_policy.py -q --tb=short`：58通过。
- 真实本地TCP：正常响应回收进程；持续滴流2秒总超时后源端观察连接关闭；超过配置响应大小被拒绝；Worker被kill后源端观察socket owner退出；无遗留可见子进程。Worker死亡测试的私有临时残留只置于pytest隔离目录。本地受控源不计供应商异常接入。
- 受控账本验证临近到期时禁止出站、最大请求配置尊重有效预留。已有超预算零出站、失败扣账、最终保存失败关闭及线程作用域隔离回归通过。
- Ruff通过、Mypy四个变更模块通过、OpenSpec验证通过。
- 实际部署collector镜像 `sha256:b8d45ced83b56cb7ba41ea29175551f3813169a07385a88976628663a274c8df`；保留独立数据库/归档，使用本地host网络覆盖配置，实际验证入口13000。
- 部署后实际UI主链8项通过：单股5m384记录、所选因子5点、两成员96行情，消费者及刷新、桌面手机/只读/作用域拒绝。财务六披露版本与31条事件UI分别通过桌面手机、刷新及axe检查。
- `verification-independent-collection.py`启用原始捕获及质量验证，4个新非空发布的公开分页/raw/standard摘要、原始捕获/质量及消费者结果复核通过，不仅检查任务接受或服务健康。
- 仅新部署后账本快照：factors.history 1次/5信用/298字节/651ms，metadata.calendar 1次/1信用/2675字节/914ms，metadata.names 1次/1信用/290158字节/9857ms，research.data 2次/2信用/9494字节/最大2576ms，全部finished。耗时含进程启动及HTTP，不含pacing及预留等待；这些只是样本诊断，不作为1/5/20池性能基线。本次行情使用缓存，没有新增bars.history RPC。

## 未验收范围

主机长暂停/任意墙钟跳变、多实例全部路径竞争、取消执行中HTTP、旧股票池Adapter独立出站、调度/复核集成、公开用量/拒绝事件及UI、公平领取未完成。进程启动CPU/内存开销需纳入既定性能基线。完整传输异常原始留存仍未完成。上述局部测试和真实成功不证明完整5.1/5.3交付。
