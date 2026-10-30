# 定时槽到既有任务的事务准入

本阶段推进7.1/7.3，整体仍14/42。新PostgresScheduledJobs为内部adapter，未装配timer、公开HTTP/UI启用、来源发布资格或实际自动任务，不声明自动采集交付。

## 保证与接口

adapter满足现有JobRepository接口，绑定一份ScheduleLease和已持久保留的run ID。正常CollectionService继续规划、查权、规模/确认检查、作用域串行提交，正常Worker继续领取、当前授权、硬预算、原始归档、门禁与发布；没有独立的定时下载实现或供应商快捷路径。

只在新的任务最终写入时，以当前job事务锁schedule行，检查enabled、revision、token、UTC槽、不可变run关联及数据库实际时刻尚未到期。clock_timestamp随事务推进，不以固定的now误放行预检期间到期的租约。actor/scope/source/category/period/fields/标的必须与冻结选择一致，mode只可missing，不能借该入口扩大为全量重采。缺少事务连接拒绝。来源预检不持schedule行锁，最终锁随job事务提交结束，停用的确认与入队形成明确先后顺序。

同一已接受的完整请求仍通过原服务幂等读取，停用后不重写既有成功产物；这不是许可新入队或继续获得授权。生产尚未启用此adapter的自动调用。

## 受控验证

隔离PG用例各自使用新临时数据库，控制Provider返回一条行情并保存原始回执；真实DB/文件/普通Worker及门禁产物，不计供应商live、UI创建或定时触发。

- 3个独立连接并发提交同槽，只产生一个job；普通Worker生成非空发布，重新连接查询completed/1条，停用后同请求内容不变。
- 预检期间停用、过期、重新领取、自然等待到期四种情况，旧owner均拒绝，无任务或分段残留；新owner沿用相同run身份可提交。
- 使用普通采集支持的输入，拒绝all模式、未保留请求ID、另一标的和另一来源/周期，确认无任务。
- 新增及修改代码注释说明事务锁范围、判时依据、幂等与停止语义；核心未引入psycopg/SQL。

批准信用上限与当前估计重验、尚未入队请求的不可变范围日志、来源日历/发布时间、研究类别准入、timer/重启补跑/UI和真实来源自动闭环仍按原Goal未完成。不能因本adapter通过而勾选7.x。

`scripts/verification-research-persistence.py` 最终83项隔离回归通过（12项调度，含上述7个新增案例）；Ruff检查/格式、Mypy新adapter通过。换源拒绝初次样本使用Pro不支持的1d，在普通来源校验提前失败；改用支持的5m后穿过普通规划并到达冻结选择拒绝，未放宽实现。已有HTTP测试工具有Starlette弃用警告，不影响测试结果。

collector镜像 sha256:cbc2a21b32dec9ab4bfda2534e260c350e9b1fdc7aa12c15ee5630096d91c9cf 部署healthy；生产schedule/run表仍0/0，无新增迁移或启用。adapter包已部署，但自动调用尚未装配。

锁等待追加：受控事务仅锁住schedule行且不改行，观察pg_blocking_pids确认任务提交真正阻塞；等待直到lease到期后释放，最终拒绝且无任务。SQL WHERE可在等待前计算且未改行时不重新计算，因此取得锁后必须再次查询数据库实际时刻。83项完整隔离回归通过。

锁等待修正最终镜像 sha256:48d1c73af24508aec032d5bbd48fb184bdad341fa8b89bce6a247732b6c3a628 部署healthy，容器内新adapter导入通过。上述cbc2镜像为修正前的阶段部署，不代表包含第二次判时。

最终部署后 `QTRADE_VERIFICATION_ORIGIN=http://127.0.0.1:13000 node tests/verification-independent-collector.mjs` 8项通过：行情384、因子5、两成员池96，桌面/手机/刷新。`QTRADE_VERIFY_RAW_CAPTURES=true QTRADE_VERIFY_COLLECTION_QUALITY=true scripts/verification-independent-collection.py` 复核4个非空发布原始/标准、来源证据、门禁、分页与消费者摘要通过。现有手工采集回归不计自动调度验收。OpenSpec strict validate通过，生产新计划表仍0/0，凭证扫描通过。
