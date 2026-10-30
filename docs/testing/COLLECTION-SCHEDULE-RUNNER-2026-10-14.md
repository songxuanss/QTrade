# 已部署日线定时器与真实来源闭环

整体14/42，7.x尚未全部完成。脚本通过当前授权创建/启用一条专用非实盘空间计划，部署timer自动触发；不是UI创建夹具。真实Worker/来源/归档/发布及既有结果UI有证据，不能由此声明自动计划创建/启停/异常UI已交付。

## 实现与更新规则

ScheduleRunner一轮一计划一槽：当前主体授权→有界补跑窗口→持久保留槽→优先原恢复日志→必要时Policy准备并冻结→普通预览/批准/事务准入→推进下一槽。submitted仅为入队，业务结果仍由普通Worker/门禁/消费者验证。插入后推进失联时重领原槽，复用原请求与同任务身份，不重算范围。明确撤权、来源失效、超批准分别fenced暂停，预算拒绝/竞争deferred；当前管理员启用须原冻结主体仍有manage。

日线Policy读取既有Provider日历和预计发布时间，检查相邻cadence槽之间是否出现新预计发布。无新发布是正常calendar_skipped，不制造空价格；周频日线可在周日补齐周五。按市场本地日期计算lookback半开范围，与本地已采日期无关；季度/周频只是操作节奏，因子/财务/事件没有自己的已验证规则时拒绝，不借用价格完整性。

2026-10-14核对[官方沪深历史说明](https://www.mairuiapi.com/hsdata)：日线预计17:10更新完成。它是资格估计，不保证实际数据已发布，最终以来源响应和质量结果为准。曾尝试带.html路径无法读取，再以官方无后缀文档核对，不据错误URL作能力结论。

COLLECTOR_RUN_SCHEDULER默认true，COLLECTOR_SCHEDULER_INTERVAL_SECONDS默认2秒、合法1–60，只有enabled计划领取，创建仍默认disabled。timer独立async任务，不让定时预检阻塞手工Worker或后台元数据；日志只allowlisted scope/schedule/run/state/error，不输出请求正文/凭证。lease600秒，停止/到期最终准入fence继续生效。

## 测试与范围

- `pytest tests/test_schedule_policy.py tests/test_schedule_clock.py tests/test_schedule_dispatch.py tests/test_schedule_approval.py tests/test_collection_schedules.py tests/test_independent_collection.py -q --tb=short`：73项通过。日线预计发布、半开回看、正常休市skip、发布前运行取得上一日新发布、周日周频、因子未知策略拒绝及启用者不能替代执行授权有受控证据。
- `scripts/verification-research-persistence.py`：97项隔离PG通过（26项调度）。新6项覆盖入队→普通Worker→非空发布、入队后推进失败/租约重领/原窗口/单job、休市skip、未知来源策略暂停、撤权暂停、超旧窗口有界推进且无下载。推进故障为受控DataFailure，不是实际容器重启；完整实际重启/预算等待/取消矩阵仍待完成。
- Ruff检查/格式、Mypy核心/应用四模块通过。无新迁移，不改0015/旧冻结产物。OpenSpec7.x仍未勾选。

## 实际部署结果

镜像 sha256:99c12568338df3b63d0c357c7e3daead4dbc2ac68d329d88be214081e40c0f11 healthy，timer实际开启。`scripts/verification-schedule-live.py prepare` 使用专用已存在的非实盘workspace、实际SSO管理员当前授权和内部controls创建/启用计划，首槽UTC2026-10-14T09:10:00。未SQL回填到期、未手工调用runner或伪造调用/响应；生产部署timer自行领取，Worker真实请求来源。

`verify`：run/job `ff11e635-e88f-5ad1-80a0-92ea7e7c611c` completed，一分段、5条非空发布。冻结范围UTC `[2026-10-07T16:00:00,2026-10-14T16:00:00)`，即北京时间10月8日至15日；policy daily-price-publication.v1。发布 `d12ba8a1-926f-5ed3-975e-efbfb39b7ad1`，对应实际10月8/9/12/13/14日线。验证之后通过当前manage停用自己计划，保留日志/产物，避免后续自动运行；不删除生产数据。

`verification-schedule-result-ui.mjs`：登录13000、切换专用空间、键盘打开最近任务中的新run、记录浏览、正确版本图表、刷新及公开查询5条/同发布身份通过；桌面1440/手机390均axe0、页面无横向溢出。这是自动结果的既有UI消费者，不是自动计划创建UI。截图实际复核，图表名称农业银行/601288.SH、1d和冻结版本正确；手机图表有可聚焦的局部滚动区域，最新bar应可键盘滚动查看。

最终3项UI检查通过，新增手机聚焦图表并连续ArrowRight移动到最右侧，断言scrollLeft/clientWidth已到scrollWidth末端；截图复核可见10月14日最新bar，图表只局部滚动，不靠页面溢出。5条不足MACD/RSI/ATR时仍显示样本不足，不伪造指标。

手工主链部署后 `verification-independent-collector.mjs` 8项通过（384行情、5因子、两成员池96，桌面手机刷新）。带上述新job的 `QTRADE_VERIFY_EXTRA_JOB_IDS`、RAW_CAPTURES/QUALITY运行 `verification-independent-collection.py` 复核5个非空发布：raw/standard/HTTP来源证据、门禁、分页摘要及市场消费者数量均通过。真实定时结果与受控计划创建/故障分别记录，不以fixture证明live权限或源可用。

自动计划HTTP/UI、暂停/等待显示、实际重启/撤权及源失效矩阵、因子周季/财务事件策略、迟到修订、新鲜度/告警和完整性能/恢复交付继续未完成。只有这一条有界日线真实自动结果，不外推全市场或全部类别。
