# 固定年度股票池性能复测

OpenSpec 9.4：同一部署、证券、日期、来源、周期和 all 重采模式复测，预先设定的响应、消费者可见和采样内存目标全部通过。原基线报告及私有证据保留，未以复测覆盖基线。单轮结果不能证明 p95 或全市场容量。

## 条件与证据

固定条件见 [基线](COLLECTION-PERFORMANCE-BASELINE-2026-10-14.md)及 `docs/data/COLLECTION-ACCEPTANCE-SCOPE.md`：13000 UI→3122认证API→18100独立采集/PG/Worker→实际授权MaiRui→归档/门禁/发布→市场消费；未复权日线，北京时间 `[2025-01-01,2026-01-01)`，固定20股票的前1/5/20子集，来源间隔3秒，原额度和持久卷。镜像沿用基线，无产品变更。元数据缓存为实际混合暖状态，不称冷启动；日线不使用Pro往年分钟缓存。

测试脚本将 baseline/retest 输出分开，检查固定输入相同；保存发布时消费者状态与最终全 visible 状态。全部26个分段新产物，每股243条，不利用已有覆盖减少重采，也没有删换失败证券。本轮未出现源失败、重试或空响应；故障恢复证据另见 [空响应恢复](COLLECTION-EMPTY-RECOVERY-2026-10-14.md)。

## 响应与最终结果

| 规模 | job ID | 预览ms | 接受ms | 发布结果ms | 全部消费者可见ms | 非空条数 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 1b858917-2c32-4c69-9763-db295fb3323a | 1221 | 317 | 5079 | 7278 | 243 |
| 5 | 48ad11d6-3ced-496c-84e8-5d5f15ca5aad | 634 | 306 | 14717 | 15056 | 1215 |
| 20 | 65bdca3f-18af-4b38-a495-a5aebbcf5550 | 1230 | 303 | 59639 | 59876 | 4860 |

预览≤3000ms、接受≤2000ms、全部visible≤25000/60000/180000ms均通过，刷新后状态正确。与基线visible 7793/15484/59764ms相比，复测7278/15056/59876ms；没有宣称优化带来显著提速。

## 实际调用与阶段耗时

| 规模 | Worker请求/信用 | HTTP字节 | 传输ms | 节流等待ms | provider阶段ms | Worker阶段ms | 非节流/传输残余ms |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 2/2 | 28059 | 333 | 2972 | 4345 | 4512 | 1207 |
| 5 | 5/5 | 130226 | 1380 | 5905 | 9906 | 10679 | 3394 |
| 20 | 20/20 | 524059 | 4800 | 28122 | 44404 | 47086 | 14164 |

首组包含2025日历请求。全部27次传输有计时，request ID关联真实账本，数量/信用/响应字节一致、失败0。仅统计这些运行的Worker请求，不包括UI预检元数据和全局后台刷新。transport包含网络/正文和少量本地写入/关闭，排除spawn/pacing；不是供应商服务器计算时间。残余包含进程启动、鉴权、JSON、归档、门禁和SQL，不能当纯CPU。阶段总和不能外推并发系统墙钟。

## 资源与存储

| 规模 | collector CPU峰% | collector内存MiB | PG CPU峰% | PG内存MiB | 每容器样本数 | 对象文件/字节 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 9.90 | 95.70 | 10.96 | 47.96 | 2 | 12/94471 |
| 5 | 46.05 | 134.10 | 15.99 | 46.04 | 3 | 40/446678 |
| 20 | 53.96 | 151.00 | 13.86 | 46.12 | 15 | 160/1803792 |

collector采样内存≤256MiB、自有PG≤128MiB均通过。约4秒Docker离散采样峰值不代表瞬时硬上限，不涵盖其他API/Worker/市场存储资源。对象目录实测含raw/standard/manifest/回执/质量，不包括PG表索引、共享缓存和消费者复制存储；未将这些数值当项目总容量。

## 实际重启与只读复验

全部复测完成后实际重启collector，ready返回成功，再验证30个非空发布（4个既有及26个复测产物）：原始/标准摘要、HTTP源回执、质量检查、公开分页和真实消费者数量一致。重新登录原空间，逐组验证全部part completed、consumer visible、每股243；各自独立601288.SH发布的图表名称、固定dataset ID及刷新正确。图表按现有30天窗口读取，全年243条由三页公开查询核对，不把窗口图表当全年图表。

此前两条只读检查的终端输出丢失；确认进程已终止后重跑只读检查，两者退出0。不重新提交下载或重启Worker。重启命令耗时不是ready恢复延迟；本轮没有精确测量启动延迟。这是完成产物的持久恢复，执行中恢复、调度补跑及隔离备份恢复继续保留未完成。

## 可复核命令

- `.venv/bin/python scripts/verification-collection-baseline.py retest`：真实UI三组完成。
- `.venv/bin/python scripts/verification-collection-baseline-metrics.py retest`：阶段、账本和对象指标提取。
- `.venv/bin/python scripts/verification-collection-performance-targets.py`：固定输入及每组9项检查全部通过；失败时先保存结果再拒绝，不降低目标。
- `QTRADE_VERIFY_EXTRA_JOB_IDS=<上述三个ID> QTRADE_VERIFY_RAW_CAPTURES=true QTRADE_VERIFY_COLLECTION_QUALITY=true PYTHONPATH=packages/data-contracts/src:packages/data-core/src:adapters/data_http/src .venv/bin/python scripts/verification-independent-collection.py`：30非空发布核验通过。
- 在 `apps/web`：`QTRADE_BASELINE_PHASE=retest node tests/verification-collection-baseline-recovery-ui.mjs`：代表图表/版本/刷新通过。

私有本地JSON输出权限0600，不提交原始配置、账号凭证或源URL。本轮仅变更验证脚本及文档；既有产品测试54项记录见基线，不冒充本轮重新执行。9.4完成，其他主链、质量、新鲜度、调度恢复和备份验收继续按原目标推进。
