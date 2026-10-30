# 供应商JSON拒绝后的持久化验证（2026-10-25）

完整Goal保持19/42；本轮补强4.1证据，不勾选完整质量门禁或真实接入任务。生产行为未新增，仅增加原严格JSON修复的跨层回归。

## 新增24项

- 财务/事件各8项：重复业务码、转义后同名键、NaN、Infinity、布尔/小数/null业务码、不可表示指数。真实PostgreSQL与私有文件先保存精确HTTP响应；解码拒绝后重新打开索引与归档仍可读取完整字节；无解析RAW页、标准产物或发布，文件权限0600。
- 行情/因子各4项：重复业务码、转义重复键、NaN、布尔业务码。真实队列/Worker消费，恰好一次受控HTTP调用，持久状态记录unavailable，无发布、无标准化检查点或质量通过记录；重新打开文件可验证精确响应，旧发布与全部旧文件字节保持。

HTTP为MockTransport受控响应；数据库、队列及文件为实际适配器。研究路径通过executor执行，行情/因子通过Worker入口`run_once`；不是独立部署UI或真实MaiRui成功，不用这些用例代替主验收链。

## 执行及结果

Ubuntu26根锁文件Python环境：

```sh
.venv/bin/python scripts/selftest-research-acquisition.py
.venv/bin/python scripts/selftest-research-acquisition.py --collection-only
.venv/bin/python -m ruff check tests/test_research_acquisition_postgres.py tests/test_supplier_json_persistence_postgres.py scripts/selftest-research-acquisition.py
```

脚本为每组创建随机`research_test_`隔离数据库，在finally仅删除本次持有的数据库；队列组分库，异常用例结束取消自身延迟重试，不操作已部署业务库/产物。

第一轮前七组98项通过；最后一组17通过、4失败。4项行情异常任务复用了前置成功任务的一年缓存，未命中HTTP，因此失败不证明解码器失效。测试改为独立新缓存并断言恰好一次请求；仅重跑受影响最后组21项全部通过。最终8组共119个不同用例通过（含新增24项），不宣称第一轮全绿。Ruff格式与检查通过。FastAPI TestClient有既有Starlette/httpx弃用警告，未为消除警告改变锁文件。

代码：[研究响应持久化测试](../../tests/test_research_acquisition_postgres.py)、[行情/因子Worker持久化测试](../../tests/test_supplier_json_persistence_postgres.py)。本轮无供应商秘密，固定fixture标识不冒充授权；真实供应商仍受候选许可404阻塞。

## 六主业务影响

登录/权限沿用[Ubuntu26真实部署验收](WINDOWS-UBUNTU26-ACCEPTANCE-2026-10-25.md)。数据采集新增本报告受控拒绝/持久化/旧产物保持证据；真实下载、质量发布、消费者刷新仍未通过。策略版本、回测/报告、告警、连接交易控制本轮未改未测，不新增就绪声明。完整类别质量规则、全部故障恢复、真实来源及UI矩阵继续未完成。
