# 技术设计

## Context

见proposal。data-contracts/core及参考Adapter独立wheel已存在；下载执行、研究发布与兼容campaign仍在QTrade。现有用户任务正在运行，不能因新服务迁移删除或重置。

## Goals / Non-Goals

小Interface覆盖目录、计划、提交、查询、控制和产物；独立采集不依赖策略/交易/业务鉴权类型。市场终端保留消费职责。全市场实时、历史财务PIT、复权与新付费来源沿用原计划，本次不扩展。

## Decisions

0. Instrument Identity把资产类别、市场、交易场所和产品代码独立表达。保留现有A股/指数ID兼容，其他品种使用显式命名空间；未来期货具体/连续合约、加密现货/永续分别标识。核心不推断币种、交易规则、交易时段或六位代码；A股规则仅存在Adapter。名称由参考接口解析并冻结到计划/产物，显示端使用名称加代码，未知明确未核验而不编造。

1. 同仓独立wheel与独立镜像，复用data包，不复制QTrade进程。独立PostgreSQL任务库、持久Parquet归档Adapter；进程以租约处理子任务，避免引入业务数据库共享。相比仅提取目录，远程HTTP seam检验真实独立性；相比立即迁出仓库，保持契约和消费者可同时验证。
2. 数据契约定义类别、SourceDataset能力、冻结CollectionJob、Publication及标准Bar/FactorPoint。来源Adapter解释路径、元数据、单位和标签；core只做计划、幂等、重试与归档。Bar和因子不共用价格模型。
3. 请求使用服务身份，携带主体/作用域；独立DataAuthorizer可由部署方提供，QTrade提供受认证的实时授权回查接口。API与Worker都校验；回查不可用时等待，禁止默认放行。不跨网络传供应商秘密。
4. 普通Bar与因子子任务七天；Pro较早年份按年归档缓存后裁剪实际请求区间，避免每七天重拉年片。当前年采用普通历史接口；年片数据不因定期刷新静默改写旧产物。来源请求节奏和缓存集中在采集Adapter，安全记录HTTP状态/来源代码。
5. 清单冻结原始/标准摘要与来源元数据。QTrade导入Adapter通过分页HTTP读取并验证，先写市场数据候选再提交可见metadata，保证幂等。因子在数据管理浏览，因子不进入K线模型。业务Worker只同步已发布产物，供应商接入留采集服务。
6. 新下载入口支持股票池范围与手工股票/指数。旧采集任务与campaign兼容消费继续至完成，标记旧路径；不是声明旧未完成portable任务已经迁移。新服务所有任务和归档独立存储，后续移仓只需移动独立包与部署文件。
7. JSON日志白名单字段含correlation_id/job_id/part_id/source/stage/attempt/duration/error/http_status/provider_code，拒绝任意异常文本。入口、worker和来源均输出，Docker轮转限制磁盘。正常日志不含原始URL、响应正文或凭证。

## Risks / Trade-offs

权限回查依赖QTrade网络→不可用等待，避免下载中绕过撤权；独立调用方可换授权Adapter。年片大量记录→有界响应与年度缓存，标准分页不一次传全量。因子历史可能不足→明确实际覆盖及PIT未验证。旧任务共存→页面明确路径，新服务不接管不兼容旧表。

## Migration Plan

QTrade原生与Compose客户端分别使用可达地址配置；主镜像包含HTTP Adapter但不运行采集进程，避免容器误用主机loopback。原生验证启动也应用独立市场迁移。先契约/独立任务存储，再来源Adapter及服务/日志，随后QTrade客户端和产物导入，最后UI和独立部署验收。关闭新采集配置可恢复原入口；新产物仍保留不可变，旧数据集引用不改写。第二台物理机器未提供，验收独立容器/数据库及HTTP，文档提供跨机器所需地址与授权配置。

## 验收发现的回归修正

2026-10-12 主部署访问审计：默认 Compose Web 未传入 OIDC 配置，13000 数据页只有权限占位，登录返回 503；主 API 未配置远程 collector。3120 原生生产构建已有认证 API、持久业务库和独立采集服务，不能凭该入口宣称 13000 可用。修复 Compose 的显式配置传递；本机认证 override 使用 host 网络访问本机 SSO 与已部署认证 API，仍通过公开接口，不绕过鉴权、不更换或删除原业务库。13000 实际消费 API 地址与其他服务连接必须记录；外部正式部署使用可达 HTTPS 地址及普通容器网络，不使用本机 override。

Web认证浏览器替身的RuntimeStore沿用旧transition签名，遗漏新audit参数；仅对齐测试替身签名及审计留存，验证停用控制的UI回归，不声明真实交易/订单能力已就绪。行情查询用psycopg安全SQL组合并保留参数绑定，消除静态安全检查警报。npm audit报告已有braces开发工具链高危和DOMPurify低危，未采用自动跨主版本降级，记录在验收限制；生产依赖high阈值检查通过。
