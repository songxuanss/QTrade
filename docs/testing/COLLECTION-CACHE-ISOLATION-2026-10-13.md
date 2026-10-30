# 采集缓存并发隔离阶段

## 问题与实现

此前 MaiRui Adapter 的因子目录、名称、交易日历和年度行情共用 RLock，且锁内调用供应商。一个慢元数据请求会阻塞无关资源，即使它们的缓存已经可用。现在改为按因子目录、名称分组、日历年度、行情标的/周期/年度分别串行化；锁注册表不包含网络等待。同一年度行情仍只取一次，随后调用者留存自己的 cached_history 血缘。

注释说明锁的范围及原因。OpenSpec design 同步该机制及限制：它只保证同一 Adapter 进程，不能替代跨进程预算或持久目录后台刷新；6.5 不因此勾选。

## 受控验证

- `.venv/bin/python -m pytest tests/test_mairui_cache_concurrency.py tests/test_independent_collection.py -q --tb=short`：31 项通过。
- 新测试把因子 HTTP 请求停在 Event 上，在释放它之前验证名称、交易日历及年度行情完成；不是依靠供应商响应速度或固定 sleep。
- 同年片并发测试验证一次供应商请求、一个缓存文件，两个调用者各有 history/cached_history 留存。
- 对修改文件执行 ruff check/format 和 mypy：通过。

测试供应商响应为 fixture，不证明外部服务可用、账户授权或新下载已完成。部署和实际 UI 回归另记于下节。

## 部署与真实页面回归

- 按 `.env.collector` 独立部署 collector，保留数据库与对象卷；当前镜像 `sha256:e6a628dee3fcad8d122a801a49309a7c940c30f43c4a4ad8c5d3d66254d4c463`。
- 实际入口 `http://127.0.0.1:13000/workspace/data`，认证业务 API 3122，独立 collector 18100。
- `cd apps/web && node tests/verification-collection-quality-ui.mjs` 最终 7 项通过：日线 passed、因子 warning、无观测、旧任务未取得检查报告的实际持久状态和刷新；跨任务分段 404；1440/390 布局无页面溢出且采集区域 axe 0，检查按钮键盘可操作。
- 首轮因未找到指定年度任务而失败，浏览器返回不含任务参数的普通数据页。脚本原先在同意授权后没有等待最终返回便打开任务 URL，存在导航竞争；补齐返回与采集区域可见等待后重跑通过。没有修改断言或改换任务来掩盖失败。
- 脚本 prettier/eslint 通过。私有证据 `collection-quality-ui-results.json`，不包含凭证。

此回归读取既有真实生产链路产物，不是本阶段新发起下载，也不证明外部目录恢复。安全筛选的供应商日志记录两次约 5 秒请求结束、未带 HTTP 状态，不能据此推断无历史或权限拒绝。持久目录快照、后台更新、新鲜度、预算、调度及完整新采集验收仍未完成；整体进度保持 12/42。
