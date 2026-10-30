# 持久后台证据核验

本阶段接通现有A股未复权日线的明确启停、后台核验与失败展示。**没有隐式下载最新行情，也不把后台执行或旧历史解释为来源最新状态已确认。** 其他类别规则、最新来源独立观测、公开全局健康视图与真实来源异常到告警恢复仍未完成，整体保持17/42，8.1–8.3不整体勾选。

## 设计、接口与 review 要点

- 0019追加watch和不可变配置修订日志，scope/完整key唯一。默认停用，默认900秒；共享创建契约允许60–86400秒，当前UI常用操作固定每15分钟。创建重复返回已有配置，不静默覆盖周期或启停。显式启用以当前会话主体执行并记新revision；停用清理租约，保留检查、错误及事件。配置日志记录实际控制人，payload中的执行主体与控制人有意分开。
- 独立后台每5秒最多领取一个应执行watch，SKIP LOCKED、600秒租约、token/revision/subject/key/真实DB到期检查。停用/改主体fence旧执行；源码中在短watch/key事务的前后复核租约，过期连同快照/outbox回滚。当前管理权限在出站准备前及提交时重查；撤权/未支持暂停，预算和暂时故障按配置周期退避。关闭COLLECTOR_RUN_FRESHNESS只停止后台领取，保留配置；默认true，仅接受true/false。
- 不复制下载或质量管道。按scope/source/标的/日线/未复权/mapping找到新发布候选，再通过原FreshnessCheck验证精确归档、质量与获取时刻；候选新不等于完整。已有更强checkpoint由原子合并保留，未知不推导为采集或来源滞后。元数据调用复用usage_scope、缓存、限流和预算，没有新行情请求。
- 公开严格Watch/Create/Control契约及JSON Schema；collector服务身份和当前READ/管理授权，QTrade管理+CSRF控制。客户端不能提交actor/scope/next_due/lease。只读可按scoped发布读取，控制按revision防旧响应覆盖，新启用明确采用当前操作者身份。
- 保存独立last_attempt_at。较新的后台失败覆盖较旧健康展示，即使快照年龄TTL未到；配置操作不抹掉失败或重标其时钟，较新人工成功可覆盖较旧失败。last_checked_at表示已保存核验，unknown也可为一次有效检查，不能称数据健康。
- UI默认折叠后台配置，避免多标的结果反复堆叠说明。展示周期、最近保存、下次应执行、问题与恢复操作；错误为可理解中文。明确启用不下载行情，启停与数据健康分开。失败保留旧证据并待核验，不能继续借旧current显示健康。关键领域、时间、授权和并发约束均有注释。

## 受控验证

`.venv/bin/python scripts/selftest-freshness.py`：201项通过，使用新建唯一名隔离PG数据库，仅清理自有库。包括原契约/归档/事件/投递/架构，以及：

- 默认停用、重复创建不覆写、启停/CAS与新主体接管、不可变修订日志；同watch12并发仅一个owner，到期换token、旧owner拒绝；停用中旧执行不能提交；已写入后模拟到期，快照与outbox一起回滚。
- 当前撤权在来源准备前暂停，预算失败有界退避且不写成功时间；真实文件/PG发布候选/HTTP创建→启用→Runner核验保存→READ查询→受控撤权暂停，不伪造来源请求成功。
- 会话推导scope/actor、READ写拒绝、严格布尔/版本/身份、错watch key拒绝；较新失败在TTL内标待核验，较旧失败不覆盖新人工检查，配置时钟不重新激活旧失败。

日历、授权故障与租约到期为受控输入，不能计供应商实接或真实供应商停机证据。Ruff/Mypy、Web TypeScript/ESLint/Prettier、生产构建和OpenSpec严格校验通过。

## 实际部署 UI 与持久结果

13000 Web→3122当前会话API→18100collector→独立PG/归档及后台Worker。使用此前真实UI/Worker/授权来源产生的601288.SH年度归档，没有新价格下载。当前预期周期来自真实来源日历/缓存，旧发布截至2025-12-31仍为unknown，不假称来源已更新。

`cd apps/web && QTRADE_VERIFY_WATCH_RESTART=true node tests/verification-freshness-watch-ui.mjs`：

- 实际SSO管理员1440桌面，从原任务定位精确发布；创建默认停用、键盘明确启用，真实独立后台Runner保存新checked_at，当前授权/归档/质量及snapshot合并通过。界面和API均仍unknown，watch_issue为null。
- 启用状态实际重启collector，enabled、revision、next_due_at和已完成检查时间保持，未立即重复检查；不是执行中杀死后有界补跑的全矩阵证明。执行中到期/停用fencing另在隔离PG受控验证。
- 停用后跨后台轮次检查时间不再变化；刷新重开保持。最终专用验证watch为停用，保留记录供review。脚本失败时只对其自有验证watch尝试认证停用，不修改无关watch。
- 采集只读risk_manager在390手机可读，启停按钮隐藏，携带有效CSRF直接创建/控制也403；匿名/跨空间及缺CSRF拒绝。此主体仍拥有其正常告警处置权限，不称全站只读。
- 桌面/手机局部布局、无页面横向溢出、axe0及键盘通过，截图已观察。后台区域默认折叠，需要时展开，不自动对每个发布探测来源。

`.venv/bin/python scripts/verification-freshness-watch.py`验证0019实际校验和、连续配置revision、不可变payload摘要、真实unknown快照与停止状态、旧数据截至2025-12-31；没有为unknown伪造过期或恢复事件。私有结果/截图在review目录，不含凭证。首轮此只读脚本误比较Z与+00:00文本，改为有时区datetime等价比较后验证，未将字符串差异当业务数据错误。

镜像及最终回归结果在本次review产物与下文记录；nativeAPI3122保留原配置安全重启，collector与Web使用原持久卷，不重建用户数据。

## 未完成边界

当前自动检查的是已有发布证据和更新规则；没有独立探测最新价格，故旧年度数据正常保持unknown而不生成来源滞后告警。只有受控producer的投递已验证，真实来源迟到/恢复到通知尚需接入实际来源观察。因子/财务/事件的不同更新规则、全局健康与积压视图、全部撤权/停机/恢复矩阵、质量重处理及完整数据主干仍继续推进，不从本阶段检查通过推导完整研究或交易就绪。


最终collector镜像 `sha256:5a21ba828c68f76800084d760b854e4082a455572a8cc2d67d7d984e993662b5`，Web镜像 `sha256:945f1e35168687ed5290a72e4b015b54e39a8b8837afbb1fb76e3707de23c1d8`。最终折叠UI及停用错误提示文案部署后，带实际重启的完整管理员/采集只读桌面手机脚本再次通过；0019与修订日志复核通过，验证watch为停用。`verification-daily-freshness-evidence.py`再次通过三份真实年度发布的raw/standard/质量重放与公开查询，未新写测试快照或探测最新价格。
