# 新鲜度事件投递与部署恢复验证

本阶段接通独立采集 outbox→公开 HTTP→QTrade Worker→既有告警与站内通知。**异常 producer 是受控 fixture，实际投递链路与通知落库为真实集成；未证明供应商真实故障或自动核验已就绪。** OpenSpec 仍17/42，8.2的公开健康/自动核验和完整故障恢复范围尚未完成。

## 实现及 review 约束

- 追加0018，把可变消费状态与0017不可变事实分离。单consumer/作用域/notice唯一；每次至多安装100个槽并领取1–100条，60秒租约、随机token、数据库实际时钟及5秒锁等待，SKIP LOCKED竞争。同完整key的前一事件未确认时恢复不可越过，其他key可推进。过期重领换token，旧token拒绝，已接受相同token确认幂等。
- Claim/Lease/Ack有独立严格共享契约与JSON Schema。claim只接受服务token及配置consumer，默认qtrade-alerts；ack额外检查当前manage，并绑定scope/原actor/consumer/token。撤权不消费，不创建无限重复任务；不通过浏览器代理开放全局库存。
- QTrade consumer从公开HTTP取事实，当前WorkspaceAccess复查DATA_MANAGE，使用稳定source_event_id写现有AlertEventService和站内NotificationDeliveryQueue，持久接受后才ack。事件与队列之间、远端确认前中断都可幂等重试。确认表示应用持久接收，最终渠道送达由原通知Worker负责，不能把queued称作已通知。
- 恢复是与原异常同episode关联的信息事件；不覆盖人工处置、不自动关闭原事件。没有新增邮件、Webhook或实盘行为。日志不记录原始异常、上游URL或凭证。模块通过契约解耦，采集实现不导入QTrade告警领域。

`COLLECTOR_FRESHNESS_CONSUMER_ID`在独立采集与消费Worker两端保持一致，默认qtrade-alerts；已加入Compose及环境样例。租约到期后自动重试，成功事实与通知不删除。consumer停机恢复依靠持久槽和稳定ID，而非内存位置。

## 受控测试

`.venv/bin/python scripts/selftest-freshness.py`：在本地collector loopback15436上创建唯一research_test数据库，只清理本次自有库；真实迁移/PG及QTrade隔离schema，189项通过。包括并发只取一个head、同key顺序、过期重领、错actor/scope/token/consumer拒绝、当前只读拒绝、额外字段与布尔限额拒绝、事件及通知队列之间故障、确认失败后的幂等重放、损坏scope拒绝。行情/日历/异常是受控输入，不计供应商实接。

Ruff/Mypy、契约导出及OpenSpec严格校验通过。旧schema不改写，仅追加三个投递schema。

## 部署、实际结果与边界

collector镜像 `sha256:4d592cf840d26fe4c35f10705955234fd38acd974028ffc6edfee20002546eca` 已部署，0018实际迁移；原3123业务Worker已保留配置重启并消费。用户入口13000，认证API3122，SSO3121，collector18100均健康。默认18000不是本次认证验收入口。

环境在本轮开始时发生过重启：原依赖容器退出、原native进程消失、13000不可访问。恢复原PostgreSQL/行情库/对象存储/缓存与Web容器、原SSO及API数据库和身份密钥；未prepare/reset账户或数据。SSO当前源依赖qrcode/pyotp/prometheus-client未完整安装且已有数据库落后，导致第一轮登录导航超时。安装当前兄弟项目声明依赖并应用其增量Alembic迁移后，真实登录及页面验证通过；该首轮失败没有计为通过。私有process身份记录已更新，仍是本地验证部署，不冒称生产服务管理/跨机器重启验收完成。

`.venv/bin/python scripts/verification-freshness-delivery.py`：专用非实盘验证空间、source明确为verification.freshness.delivery。持久fixture plan可恢复，重复执行复用相同notice，不重建异常。受控开启→重复异常→恢复，实际outbox只有2个notice、同episode；真实collector HTTP/业务Worker写2条告警，通知Worker成功落库2条in_app，collector两条ack均delivered。不是新行情下载或供应商故障探测。

`cd apps/web && node tests/verification-freshness-alerts-ui.mjs`：真实SSO管理员1440桌面、采集只读的risk_manager主体390手机；开启/恢复详情、source与episode上下文、刷新持久及跨空间拒绝通过，axe0、页面无横向溢出。截图已观察：桌面事件流+详情、手机单列；risk_manager的告警处置权限与其采集只读权限分开，不冒称全站只读；通知最终落库由实际PG单独核对。测试查询精确event身份，不借其他事件或缓存冒充成功。私有结果/截图在review目录。

## 仍需完成

公开健康视图、持久自动核验及其当前授权/重启恢复、真实来源过期→通知→恢复，以及其他类别日历规则和全部主业务矩阵仍需交付。此处fixture producer的真实渠道验证不等于这些业务能力已经通过。保留全部原goal要求，不勾选完整8.2或声明目标完成。


原`verification-freshness-ui.mjs`在恢复后的13000环境再次完整通过：真实旧年度快照、管理员重新核验、采集只读角色拒绝管理操作、过期/读取失败恢复及刷新。未改写旧归档，不借受控事件代替真实行情。`scripts/selftest-freshness.py`最终版再次189项通过。
