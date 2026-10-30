# 真实空响应后的原任务恢复

整体仍15/42。此次增强真实异常恢复证据，不新增来源、不更改固定验收范围，不将原空响应改成成功。没有生产采集算法变更；端到端恢复使用既有正式UI/API/Worker/真实来源。

## 原问题与有界探查

此前招商银行日线job `5f8ba9f1-fdcd-564b-9876-12bfc0bf7815` 的北京时间10月8日至15日半开请求，history回执response_rows=0，no_data且未发布。检查实现：MINUTES['1d']=0，日线不减一天，et为半开end减一微秒；没有发现声明的一天偏移。不能把空响应解释为权限不足、停牌或固定历史长度。

诊断起初在docker python stdin中执行，但生产有界HTTP使用spawn子进程，stdin入口不可重载。该诊断只得unavailable/无捕获，不是新供应商结果；改为守卫main的真实脚本文件 `scripts/verification-schedule-source-probe.py` 后，当前管理授权、同出站预算及原始回执链路有界请求三个固定窗口，非队列下载/发布：

| 标的/日线窗口（北京时间） | 当前标准记录 | 原始证据 |
| --- | --- | --- |
| 600036.SH，2026-10-08至15 | 5 | HTTP及history 5行 |
| 600036.SH，2026-10-13至14 | 1 | HTTP及history 1行 |
| 600036.SH，2025-09-01至02 | 1 | HTTP及history 1行 |

全部调用权限/额度与生产一样，不输出URL/请求体/凭证。诊断直接provider获取及私有回执不计完整队列或UI创建验收。Source相同原范围先0后5，具体供应商变化原因未确认；已排除从该空响应推导永远不支持该标的/范围。

## UI原任务重试与最终核验

管理员登录13000、切换同专用非实盘空间、从最近任务选择原招商银行1d，先验证no_data/所有publication为null，再用键盘执行“重试失败或空分段”。不重新创建计划、job或part，不修改原日期、源、执行主体或映射。Worker再次真实出站后，数据库确认原job completed、5条发布。

首轮验证脚本在Enter触发异步server action后马上GET，将旧no_data当新终态提前结束观察。实际动作已接受且Worker随后完成；没有再次重发。脚本已等待server action响应再解释结果，并以显式 `--observe-recovered` 对已完成原任务做后续只读验证，报告标记此模式，不假装再次经历初始状态。

`verification-empty-schedule-retry-ui.mjs --observe-recovered` 通过：原job/part身份和请求、非空消费者、招商银行/600036.SH正确1d冻结版本图表、刷新后同发布5条、桌面1440/手机390 axe0/无页面横向溢出。首轮空状态与键盘重试动作来自第一次脚本记录，后续只读核验来自观察模式，两者区分。

独立数据库与归档再核对同part的history回执数量同时保留0与5，completed/publication.record_count=5；原空响应不可变证据未删除、没有用当前5条覆盖旧0。带该job运行 `verification-independent-collection.py` RAW_CAPTURES/QUALITY，公开分页、raw/standard/HTTP证据、规则和市场消费摘要通过。该恢复未开启定时计划，4条既有验证计划保持disabled，旧成功产物不改写。

原响应0仍属真实来源异常证据，恢复后的5才是本次成功；不外推其他标的/时段，不由此完成整个恢复、修订、备份及新鲜度矩阵。OpenSpec原任务范围继续，Goal未完成。
