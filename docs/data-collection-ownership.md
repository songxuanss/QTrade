# 采集模块的数据所有权

`qtrade-data-contracts` 定义跨进程的版本化 DTO；`qtrade-data-core` 只接收作用域、
参与者和动作授权接口，不读取 QTrade 的用户、策略、交易表。MaiRui 和 PostgreSQL
实现分别作为独立发行包。当前已验证四个 wheel 在没有 QTrade 私有业务包的环境
安装、导入；这不代表独立采集 Worker 或远程服务已经交付。

参考成员由采集模块拥有：`qtrade_data.universe_snapshot` 保存完整不可变快照、
内容哈希、来源观察时间、参考差异、分类版本和创建者。成员变化生成新版本，
旧快照不更新；同一成员可有多个归属，但单快照身份和归属均禁止重复。
当前目录描述当前成员，不提供历史时点成员保证。

迁移由 `qtrade_data_postgres.migrate` 单独执行，使用独立迁移表和校验和，
仅允许采集自身 schema 内部外键，不引用 QTrade 业务表。`DATA_COLLECTION_DATABASE_URL` 可指向独立数据库；未设置时
当前 QTrade composition 使用 `DATABASE_URL` 的独立 schema。生产应用账号应只获
此 schema 所需权限，并在每个事务明确设置 scope；独立测试实际验证了无默认 scope
和跨 scope 的 RLS。QTrade 的角色映射只位于 composition Adapter，每次读取/写入
重新鉴权，解析远程成员后保存前再次检查。

原始归档、行情发布、旧下载 Worker 当前仍由既有模块拥有；后续迁移需保持批次、
数据集 ID、版本、哈希、质量报告和引用一致。不得通过复制成员模块宣称整体迁移
完成。策略、回测与交易消费将通过公开行情接口访问，不拥有采集私有状态。


## 聚合下载增量

采集 core 现提供 `CollectionGateway`，只依赖独立 contracts 和通用授权、成员、
计划仓库与执行接口。`qtrade_data.campaign_plan` 保存完整不可变预览，
`campaign_run` 原子持久化幂等运行与待展开计划引用，`campaign_work` 保存稳定
七天子身份和可查询结果。范围计算与展开按页处理；每页最多25个新增任务，
结果轮询最多100个，成员每页30个、每成员详情最多100段，聚合数量按全部记录计算。

QTrade composition 的 `ExistingCollectionExecutor` 是唯一接入既有下载、归档和
行情查询的兼容层；纯 core/采集 PG 包不导入策略或交易模块。提交旧任务与记录
展开日志跨模块不是同一事务，稳定 task UUID 及不可变请求使崩溃重放幂等。
每次提交/展开重新检查当前管理权限；作用域采用自身 RLS，创建者不是永久授权。
完成条件核验实际归档以及非空标准行情，交易日历记录不充当价格观测。

当前取消仅停止未展开工作；已创建子任务继续并更新结果，UI 明示该边界。
完整安全取消、事件 outbox、独立归档/发布 Worker、远程消费与迁移演练仍未完成。
