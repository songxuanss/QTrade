# 提案

## Why

用户已购买Pro并批准先接完整分钟历史与因子。采集执行仍依赖QTrade业务Worker和数据库，妨碍独立部署、定位问题和迁移；市场终端需要通过受管理的产物契约消费数据。

## What Changes

- 独立可安装、可容器部署的采集进程，仅依赖独立数据包与Adapter；拥有自己的任务库与持久归档，不读取策略、账户或QTrade私有表。
- 来源能力目录、冻结下载计划、七天拆分、补缺/全部重采、租约、失败重试、限流和结构化去敏日志。
- 接入MaiRui Pro分钟年片/当前年增量，以及动态因子目录和指定因子历史；实际权限或数据不足明确反馈，不自动全市场全历史抓取。
- QTrade通过HTTP提交与查询，保留旧任务恢复兼容；市场终端独立，通过标准产物清单与分页记录接口导入行情，明确采集完成与展示就绪的差异。
- 独立Compose、独立数据库与归档卷、跨机器地址配置和部署/排障文档。
- 补齐主 Web 部署的身份配置传递和本机认证连接方式，在已公布的 13000 入口完成登录、导航、刷新、会话过期返回及真实采集验收，保留原持久化数据与权限控制。
- 采集任务和市场图表显示简称与代码，冻结来源名称快照；契约可表达港股/美股、ETF、期货与加密货币，实际未接入品种明确不可下载。

## Capabilities

### New Capabilities

- `independent-data-collection`: 独立部署、来源适配、持久下载、诊断与远程授权。
- `data-publication-interface`: 采集与数据消费者间的版本化清单、记录查询和幂等导入契约。

### Modified Capabilities

无主规范变更。原有多源研究变更保持未完成，RQData/Tushare、财务PIT、复权和实时行情未纳入本次；原portable股票池任务仍保留。

## Impact

data-contracts/core、data_mairui/data_postgres、独立collector/客户端/归档Adapter、QTrade组合与UI、市场导入Worker、独立部署和真实端到端覆盖记录。

## Non-goals and Safety Boundaries

不新增采购、实盘或全市场无界下载，不以分钟冒充逐笔，不把来源因子标为已验证PIT；秘密不进日志/契约/Git。旧冻结数据与任务不删除。

## Unresolved Material Assumptions

购买后实际Pro权限、不同年份与股票覆盖、因子历史长度和字段可得性待实测。另一台物理机器尚未提供，以独立容器网络/数据库和远程HTTP检验部署接口，不宣称已在第二台物理机器验收。
