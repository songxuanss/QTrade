# 独立采集部署与定位

服务和 QTrade 分开安装、数据库和归档分开保存。当前验收是独立进程、数据库和归档卷，通过HTTP消费；本地采集使用下述host网络覆盖配置，不能将其记为独立容器网络隔离，第二台物理机器未验收。接口见 [架构规约](../architecture/independent-collection.md)。

复制 `infra/collector.env.example` 为 Git 忽略的 `.env.collector`，权限设为 600。填写供应商证书、至少 32 字符随机服务令牌、独立数据库密码和消费者授权回查地址。不提交真实文件，也不运行会打印全部配置的 Compose config。

```sh
chmod 600 .env.collector
docker compose --env-file .env.collector -f compose.collector.yaml up -d --build
curl http://127.0.0.1:18100/ready
```

当前13000本地验证环境的宿主机IPv4转发关闭，普通bridge容器无法连接供应商，而宿主机可连接。此环境的启动、重建必须使用本地覆盖文件，避免重建后重新丢失外网访问：

```sh
docker compose --env-file .env.collector -f compose.collector.yaml -f compose.collector.local.yaml up -d --build --wait
```

该Linux本地覆盖保留原数据库及归档卷；collector直接绑定127.0.0.1:18100，数据库仅发布127.0.0.1:15436，授权回查已有127.0.0.1:3122。`COLLECTOR_LOCAL_DB_PORT`和`COLLECTOR_LOCAL_AUTHORIZER_URL`可覆盖本地端口/回查URL。仅本地回查HTTP，供应商HTTPS验证保持。此配置不修改系统转发或防火墙；远端部署仍采用正常bridge/TLS配置，不能照搬本地loopback地址。

标准bridge配置下，镜像只安装七个独立 data wheel，非 root 用户，内部端口 8100。独立 PostgreSQL 17 存储 qtrade_data schema，不暴露数据库端口；归档卷保存原始/标准 Parquet、manifest 和年片缓存。HTTP 默认仅绑定本机 18100。备份数据库和归档卷；重建不要使用 down -v。

| 配置 | 说明 |
| --- | --- |
| COLLECTOR_SERVICE_TOKEN | 两端一致的私有服务身份 |
| COLLECTOR_DB_PASSWORD | 独立 DB 密码，只在采集部署 |
| MAIRUI_LICENCE | 仅采集端供应商证书 |
| COLLECTOR_MAIRUI_PRO_ENABLED | 启用能力，不证明供应商已经授权 |
| COLLECTOR_PUBLIC_RIGHTS_FEED | 默认 false；启用公开页配股条款（东财+新浪双源校验），仅限内部研究、不可再分发 |
| COLLECTOR_AUTHORIZER_URL | 回查 QTrade /internal/data/authorize |
| COLLECTOR_ALLOW_HTTP | 本地明文回查时显式 true，远程默认 HTTPS |
| COLLECTOR_SCOPE_GRANTS_JSON | 无回查时显式 actor:scope→read/manage，默认拒绝 |
| COLLECTOR_SOURCE_INTERVAL_SECONDS | DB 共享来源节奏，默认 3 秒 |
| COLLECTOR_LOG_LEVEL | 默认 INFO |
| COLLECTOR_BIND_ADDRESS / COLLECTOR_PORT | 服务绑定地址 / 默认 18100 |
| QTRADE_COLLECTOR_URL | QTrade API 和 Worker 使用的服务地址 |
| QTRADE_COLLECTOR_ALLOW_HTTP | 仅本地验证显式 true |

不用 Docker 也可分别安装 data-contracts/core、data_postgres/mairui/files/http 和 apps/collector，设置 DATA_COLLECTION_DATABASE_URL、COLLECTOR_ARCHIVE_ROOT、令牌和授权，再运行 uvicorn qtrade_collector.main:app --port 8100 --no-access-log。COLLECTOR_CONFIG_FILE 支持私有 env 文件，环境变量覆盖文件值。

机器 A 运行 QTrade API/Worker，机器 B 运行 collector 和自己的 DB/归档。B 的 COLLECTOR_AUTHORIZER_URL 指向 A 的 HTTPS API，A 的 QTRADE_COLLECTOR_URL 指向 B 的 HTTPS 入口。令牌相同，B 不持有 A 的业务数据库、对象存储或策略凭证。防火墙/代理保证双向 HTTPS 可达、授权头透传和至少 90 秒请求超时；实际下载异步运行。

标准bridge本机容器回查用 host.docker.internal:3122；当前host覆盖回查127.0.0.1:3122。验证 API 显式设置 QTRADE_VERIFICATION_API_BIND=0.0.0.0；生产使用 TLS 网关，不照搬本地 HTTP 配置。

```sh
docker compose --env-file .env.collector -f compose.collector.yaml logs --since 30m collector
```

按界面关联 UUID 用 rg 检索，日志保留 correlation_id/job_id/part_id/source/attempt/stage/duration_ms/error_code/http_status/provider_code。日志无 URL、凭证、授权头、上游正文或异常 trace；Docker 每文件 20 MB，保留 5 份。不要开启第三方 HTTP DEBUG。/health 仅表进程身份；/ready 检查数据库和后台循环，不代表来源已可用。

重启保留卷，租约十分钟后重新领取；已存在有效 manifest 则恢复，不重新请求来源。旧租约无法覆盖新执行。失败或空分段可界面重试；限流/超时/暂时不可用退避至多八次。采集完成但终端未就绪，检查 QTrade Worker 导入状态、分页/摘要校验、行情存储和 metadata，不用重复下载掩盖消费失败。已部署 SQL 迁移保持摘要锁定，只新增迁移。

QTrade 原生部署读取 QTRADE_COLLECTOR_URL；QTrade Compose 显式读取 QTRADE_COLLECTOR_COMPOSE_URL，避免把主机的 127.0.0.1 地址误当成容器地址。Compose 不配置该地址时保留旧入口，新面板显示未连接。配置为容器可达的采集 HTTPS 地址，同时将采集授权回查指向同一 QTrade 部署的 API。验收部署是13000认证Web及3122原生API（3120为另一个原生Web入口），采集回查绑定该实例，不能把另一套业务库的主体权限混用。

## 来源感知分段配置

新计划不再统一拆成七天。Pro 分钟按北京时间年度边界，日线按年度、因子按年度且最多365自然天（保证不超过365日频点）；普通分钟保留七天保守分片。`COLLECTOR_MAIRUI_PARTITION_MAX_DAYS=0` 默认不附加运维天数上限，正整数可收紧各策略，负数配置拒绝启动。来源硬限制仍生效，总请求日期默认不限。配置只影响新计划，既有任务/子任务/产物不改写，补缺仍按原覆盖范围计算。当前年普通历史获取完整性仍须门禁与实际范围验收，年度计划不等于来源保证全年度完整。

## 因子目录缓存及后台更新

工作台通过认证业务桥接 GET `data/collector/factor-catalog` 读取独立服务 GET `/v1/factor-catalog` 的 v1 快照。读接口保留当前用户/作用域 READ 检查，但不触发供应商目录请求；兼容 `/v1/factors` 的列表形状保持不变，状态消费者应采用新快照接口。

目录在独立采集对象卷的 `provider-cache/factor-catalog-current.v1.json` 原子保存，权限0600，最多1000项、文件4MB。快照摘要验证内容。旧的 `factor-catalog-{sha256}.json` 可有界导入为 stale，原文件未记录取得时间时保持未知，不以文件修改时间回填。损坏的当前文件标记 unverified，不能伪装 fresh 或采用旧文件掩盖损坏。

服务独立后台任务每60秒检查；15分钟内有效快照复用，过期取新目录，失败保留旧字段并显示安全错误码。页面“更新目录状态”通过独立服务器action只读取当前目录状态，保留正在编辑的输入和选择，不重载整页，也不强制向供应商重发请求；摘要变化时使旧预览失效。首次没有快照时显示 loading/unavailable，来源未配置时显示 unconfigured。目录存在不证明当前历史权限或可用范围；因子预览和执行仍需要有效元数据或实时重新取得，实际历史请求继续验证来源授权与响应。

该机制目前针对单 collector 进程。多实例目录刷新协调、统一用量账本、其他目录持久化仍待完成，不以本节宣称完整来源故障或远端多机器验收。上游失败时已有任务和已发布结果仍可查询；排查安全 metadata_refresh_failed 日志的来源、阶段和错误码，不输出 URL、证书或响应正文。

### 因子字段选择和元数据浏览

选择“量化因子 · 历史序列”后，可按分类筛选，按名称、代码或定义搜索。已选因子单独显示；搜索无匹配或切换分类不会删除已选项，也不会隐式全选。点击已选项可移除；checkbox支持键盘选择。实际字段变更会使旧预览失效，须重新预览才能提交，纯视图过滤保留原选择与计划。

展开“查看定义与元数据”可读取供应商定义、单位、更新频率和计算版本。未知值明确保留未知；目录没有证明历史长度、合法空值范围或研究可知时间。当前目录缺失的已选字段保持原选择并提示核查/移除，不能静默替换。输入的刷新持久化仍以“保存选择”所存偏好为准，未保存输入的完整恢复尚待实现。

### 1分钟历史边界观测

600519.SH的2025-09-01至09-10样本为8个观测日、每个观测日241条1分钟来源记录（共1928），比同来源5分钟标签展开多每日09:30一条。不能用5分钟条数乘5定义1分钟来源条数，也不能为凑240而删除来源记录。原始09:30记录按已有结束标签转换为09:29；来源时段语义未确认，发布保留边界标签、unverified语义及限制提示，不据此认证常规一分钟完整性，不未经证据归为竞价。旧清单、记录和既有时间转换不改写，完整时段规则仍须补足。

### 采集结果与冻结行情图表

消费者状态的 `dataset_id` 为QTrade市场数据集身份。采集结果的“查看图表”链接携带该身份；市场API GET `market-watch/chart` 可选 `dataset_id`，在当前空间READ权限下校验标的和周期并读取冻结版本。缺失/不匹配/跨空间拒绝，不用同股更近批次回填。未指定版本的普通终端仍查询最新观测；冻结视图显示版本及“查看最新行情”显式入口，其他周期不能借用该版本。

图表最多显示所选1/7/30日窗口，不代表整个年度产物都同时绘制。公开分页/标准摘要、消费者数据库总数验证全部产物；图表窗口数量单独记录。collector不读市场表，市场也不依赖供应商实现，连接通过公开发布及消费者身份。

## 请求信用与用量快照

`COLLECTOR_MAIRUI_DAILY_CREDITS=10000`、`COLLECTOR_SCOPE_DAILY_CREDITS=2000`为当前默认来源/空间日信用；`COLLECTOR_SOURCE_CONCURRENCY=4`、`COLLECTOR_SCOPE_CONCURRENCY=2`为并发预留限制。这些是内部防护而非供应商账单或实际授权。因子历史每次5信用，其他当前MaiRuiCollection操作1信用；出站前原子预留，失败/到期不退款，缓存命中不产生新HTTP信用。

`COLLECTOR_REQUEST_TIMEOUT_SECONDS=90`控制生产GET的整体截止，正数且最多300秒。Linux短生命周期进程持有连接，超时kill/join后再完成扣账；Worker死亡关闭socket owner。有效预留租约3600秒，出站前留10秒清理余量。每次响应解码流限制64MB。父进程或宿主机突死可能留下私有临时响应目录，不把它视为归档；不要直接打印其内容或第三方HTTP DEBUG日志。

工作台“采集用量”独立读取/刷新 `data/collector/usage` → `/v1/usage`，仍需当前空间READ许可。显示快照时刻、来源/空间当日信用、有效预留、结果与最近详情。日预算按Asia/Shanghai重置，前日仍有效预留继续占槽。各最近50条只限制明细，合计从当日全部记录算。租约到期意味着失去所有权，不证明连接仍在运行。请求耗时不含pacing/预留等待，响应字节为解码内容，失败可能只记录已保存部分，不等同网络账单。

来源/空间日信用不足需等重置或管理员调配置，预留不足需等已占用预留完成/到期后重试。新增0009安全拒绝记录与扣账执行分表；读取不会扣信用或修改租约。查询失败保留旧快照并明确非当前额度，按钮可恢复重试。共享来源仅返回预算汇总，当前空间请求/拒绝详情不含其他主体、token或供应商URL。

尚未覆盖旧股票池独立出站路径、未来定时/复核、全部实例竞争、公平领取、队列等待及存储用量；不要以该视图声称完整用量交付。相关验证见docs/testing/COLLECTION-USAGE-VIEW-2026-10-13.md。

### 原始HTTP响应诊断

行情/因子执行中，日历与历史请求的完整HTTP解码后字节在状态/JSON解析前保存私有`http_response`回执和base64封装。封装仅含HTTP状态、安全操作名、正文及complete=true，无请求URL/头；base64仅为无损编码，不能当作加密。原始正文可能含供应商诊断信息，只能在受保护对象卷中检查，禁止复制到日志、截图、报告或提交。目录700/文件600，回执绑定空间/任务/分段/租约/尝试与内容摘要。

完整401/429/503响应和损坏JSON可保留，但不能发布或视为已取得观测。HTTP回执不满足解码历史证据要求；成功仍需要history/cached_history/factor_history、转换、质量和发布。缓存复用不伪造新HTTP回执。归档失败会阻止响应消费，已发生HTTP信用不退。

该早期阶段的研究HTTP错误及部分超时正文已由下文研究回执增量补齐；全部元数据路径尚未同类覆盖，转换失败不能视为检查通过。4.1完整门禁/证据状态仍待完成。无新迁移、旧发布/质量规则不改；原始响应和解码JSON并存会增加磁盘占用，统计存储时不能只计算标准Parquet。

### 转换失败检查

已解码历史输入后的格式/转换拒绝会保存独立`CollectionProcessingFailure`，检查版本`collection-normalization-check.v1`，标准记录数量为未知，状态blocked。它不是旧质量规则的检查通过/合法无观测，不改变冻结发布和旧质量报告。记录只有安全码、版本、尝试、时间、原始回执引用，不包含原始正文或租约token。创建时核对回执和正文存在；写入失败按unavailable恢复，不能假装检查已保存。

当前READ用户通过`jobs/{job}/parts/{part}/processing-failures`（业务桥接与独立/v1同路径）读取本空间准确任务/分段的历史记录。工作台“查看质量报告”同时读取两种检查，保留失败提示，不把旧转换阻断误当当前发布状态。没有记录只能说明没有取得这类检查，不能推导数据通过门禁。研究转换检查及研究完整RAW离线重处理见后文；行情/因子及全部类别规则/重处理仍未完成。

`verification-processing-failure-fixture.py`只在已有非实盘测试空间构造明确标记的受控失败，在一事务内创建、限定领取、原始归档和阻断，生产Worker不会看到queued夹具；没有供应商调用、假信用或市场发布。对应UI脚本验证真实部署读取/刷新/权限，不证明真实供应商失败或UI任务创建。保留夹具历史供复核，不在普通用户空间准备或点击重试冒充真实下载。

研究数据的`normalize`拒绝另存v2 `ResearchProcessingFailure`，检查版本`research-normalization-check.v1`，对应PG已登记的原始批次。写入前核对计划、来源/类别/分片、字节与内容摘要，不保存异常字符串或伪造标准数量；记录文件在research-v2/空间/运行目录，相同输入不覆盖首次时间。归档故障为unavailable，原批次保留，按研究任务原手动重试/授权规则恢复。

GET `/v2/research/jobs/{request_id}/processing-failures`（业务桥接research/jobs同路径）要求当前READ及准确运行存在，返回安全元数据。财务/事件的“查看转换检查”按需读取，历史失败不代表当前发布状态，空记录不代表质量通过。`verification-research-processing-fixture.py`仅在专用测试空间用受控executor生成原始及拒绝后SQL插terminal failed行，无queued窗口，不计真实源、UI创建或租约Worker验证；不要据此推导完整4.1或回测就绪。

## 领取机会轮转

当前新队列默认采用持久来源→空间→运行轮转，SQL0010保存queue-local cursor和单调ticket。集合任务一次领取一个分段，研究队列一次领取当前整运行；新分组加入队尾，重启不重置顺序。只轮转已经可执行的工作，等待重试时刻的工作不抢占机会。事务锁只覆盖领取与游标，不覆盖供应商请求；领取失败回滚游标和租约，已完成或取消结果不被资格竞争改写。不要手工清空生产游标来调整优先级。

这是领取机会公平，不能认证相同执行时间、信用成本或跨队列统一公平。研究内部大运行尚未分区化，配置权重、预算耗尽等待及积压/等待指标仍待完成。生产大小任务同时运行的性能/公平界限还需原定验收，局部PG fixture不替代真实来源结果。报告见docs/testing/COLLECTION-FAIR-DISPATCH-2026-10-13.md。

## 行情/因子预算等待

当前行情/因子Worker区分内部预算拒绝与真实供应商限流/超时。日信用不足等待预算时区下一午夜，并发预留不足30秒后重验；这类拒绝没有出站，不消耗供应商失败重试次数，但此前成功出站的信用不退款。等待分段展示具体来源/空间预算或预留原因及下次时刻。管理者可“提前重试预算等待”，仍需当前授权和硬额度重新检查；取消等待不删除已完成产物。

单次信用高于整日日额度无法靠重置恢复，任务为预算拒绝失败，须核对配置后手动重试，避免无限按日等待。等待原因只用于waiting/failed且错误码为rate_limited，追加0011/0012保持旧迁移不改写。财务/事件研究队列已通过0013接入同类等待，并在恢复时重新检查当前授权；恢复仍重取第一页且计实际信用，未实现成功页断点跳过。部署UI真实预算拒绝/恢复还待隔离作用域测试，不把正常下载回归计作此异常路径通过。

## 财务/事件显式重试

工作台管理者可提前重试预算等待，以及重试失败/无观测研究任务。QTrade POST `data/collector/research/jobs/{request_id}/retry`通过当前DATA_MANAGE及CSRF检查，再桥接独立POST `/v2/research/jobs/{request_id}/retry`的当前manage授权。读者不能变更。重试保留原冻结请求/执行主体和已采集证据；授权撤销后不得借另一管理员的重试绕过原主体检查。

连续点击或重试已排队/执行/发布任务是幂等无变更，不替换lease或已发布引用。研究重试仍重取整个原运行并按实际HTTP计内部信用，不是已成功页的断点跳过；预算确认/重试不能越过硬限。实际预算异常UI尚待隔离作用域验收。

## 按空间配置额度

`COLLECTOR_SCOPE_LIMITS_JSON`可选，格式为规范小写UUID映射`daily_credits`和`concurrency`，两项都必须提供且为正整数，限制与默认UsagePolicy一致。只能改变指定空间的信用/并发，不能修改共享来源上限、时区或租约，也不授予该空间访问权；未知空间和后台全局元数据分别使用默认策略。重复键、非规范UUID、字符串/布尔数值、额外字段、不完整及超过1000项/64KiB配置拒绝启动，不输出输入内容。

用量快照和真实出站读取同一有效策略。配置改变需重部署collector生效；已扣信用不退款，已排队预算等待保留，管理者可提前重试且硬控制重新验证。专用预算验证脚本只移除自己创建的覆盖，其他运维配置保持。不要复制私有验证身份/配置文件到报告或提交。

## 计划规模、调用估计及确认

行情/因子新计划受 `COLLECTOR_PLAN_MAX_INSTRUMENTS=10000` 和 `COLLECTOR_PLAN_MAX_NEW_PARTS=20000` 硬限保护；输入标的数超限在来源元数据访问前拒绝，新增分段超限拒绝整个计划，不截断日期、成员或因子。已完成复用和有效在执行分段不计入新增分段。总日期仍默认不限，来源分段边界不变。

`COLLECTOR_PLAN_CONFIRM_PARTS=100` 或 `COLLECTOR_PLAN_CONFIRM_CREDITS=500` 任一达到时，预览显示核对确认框。四项均须为正整数；标的最多10000、新增/确认分段最多1000000、确认信用最多1000000000。提交的 `confirmed` 只接受 JSON 布尔值，默认 false。确认不绕过规模、当前权限或真实出站原子预算。提交重新规划并核对覆盖与配置摘要，配置改变须重新预览；已经创建的同身份任务仍幂等返回，原产物不改写。

预览的 `execution_estimate` 是后续新增分段的内部防护估计，不是供应商报价：缓存命中可能零调用，每段保守最多四次历史/日历/目录调用；因子历史按五信用加权，其他请求一信用。区间不含预检已发生的元数据、失败重试或后续复核，因此实际累计费用可能超过本次无重试估计。查询用量快照才能观察真正扣账。预览不创建下载任务，但名称/因子目录的缺失缓存可能产生已记账的元数据请求，不能宣称预览完全没有外部调用。

公开可变 `CollectionPreview` 增加可选估计、规模策略与确认字段；旧预览数据仍可解析，旧的严格客户端需升级才能接收新增字段。不可变 `CollectionPublication` 及旧 manifest/schema/摘要不改写。

财务及事件计划共用上述标的/分段硬限，在预览和提交重新规划时检查。研究目前没有逐分段补缺复用，因此其全部计划分段都计入规模，不把 `missing` 参数当作已实现复用。生成器最多消费上限加一个分段即拒绝，不先载入任意大完整范围；拒绝不留部分研究运行或排队任务。已持久化计划、已有发布及Worker恢复不改写，旧冻结计划schema和摘要不变。

研究 UI 采用独立 POST `/v2/research/plan-preview` 的 `ResearchPlanPreview`（QTrade 桥接仍为 `data/collector/research/preview`）。它在原计划字段外增加估计、策略、确认要求及 `control_sha256`；旧 POST `/v2/research/preview` 继续只返回原 `ResearchPlan`。严格解析旧计划的客户端应保留旧接口，采用新控制信息的客户端升级到新类型。提交接受 `ResearchSubmission`，将原请求字段与控制摘要、严格布尔 `confirmed` 一起发送；小范围旧请求仍可提交，达到阈值时缺少控制摘要或确认会拒绝。

提交重新规划和估计并校验摘要，策略或来源成本/能力变化使旧控制预览失效；确认不能绕过规模或信用网关。已有任务重复提交核对完整原请求且当前manage授权必须有效，不重新确认或改写产物。当前财务/有限分红适配器每分段一次无分页、一信用请求，估计0至分段数，不含重试/复核且非供应商报价。新增Provider必须提供无HTTP/持久副作用的有界估计，不把未知分页上限当作已验证成本。持久化前剥离控制字段，冻结计划/schema/摘要保持原契约。

`scripts/verification-research-confirmation.py`仅用于授权本地验证栈，临时Compose覆盖确认分段阈值为1，不改环境文件、信用、来源权益或其他作用域配置；验证财务/事件/行情/因子/池的键盘确认→真实来源/缓存证据→归档→发布/消费→刷新，finally恢复原Compose并核对实际运行策略。脚本若外部被强制终止，需要核验并恢复基线Compose；不要在同时变更部署配置的环境并行运行。数据不清除；确认/默认两组证据分别保存。

## 自动计划持久基础（尚未接通运行）

0014仅追加collector-owned collection_schedule与不可变collection_schedule_run，QTrade calendar_schedule仍只表示交易日历。严格草案冻结来源/类别/周期/字段/标的及可选成员快照引用、时区和分钟精度时间、日/周/季意图、回看及最多31天补跑参数、自动执行批准规模/信用和明确true；不能携带凭证。日/周/季意图不证明供应商更新节奏，不得据此启用未知规则。创建默认disabled，不触发任何下载。

此阶段仅有内部授权控制/PG接口，无用户HTTP启用、UI计划入口或timer连接。短领取lease最多600秒，重复enable不撤销有效owner，disable使旧token失效，advance及reserve须当前版本/所有权且数据库时间未过期；预留槽用UTC到期时间与完整草案revision派生确定身份。预留不等于已创建任务，更不能计下载成功。不要通过SQL启用生产计划代替后续来源、日历、授权、确认和配额验收。7.1–7.5继续未完成。

内部统一时钟按IANA时区计算日/周/季槽并返回UTC；首槽严格晚于创建，恢复包含游标，仅扫描配置的连续24小时窗口。DST缺失跳过，重复只取首次；窗口前只记省略界限。该操作意图尚未连接交易日历/供应商发布资格，不可据此自动提交。保留运行前复核冻结主体当前manage，明确撤权以有效租约暂停；授权临时故障拒绝本次保留，恢复后重新查权，不误当撤权。见 docs/testing/COLLECTION-SCHEDULE-STORE-2026-10-14.md。

定时任务最终入队使用内部PostgresScheduledJobs：绑定已保留槽和当前lease，沿用CollectionService/JobRepository，并在普通任务事务内复核冻结选择与有效所有权后入队。来源预检不持schedule行锁，最终短事务锁到提交结束，停用/旧owner按提交顺序拒绝。判到期使用数据库clock_timestamp，不能以事务开始时间代替当前时间。此模块仍未装配timer、公开启用或自动来源执行；不要通过SQL启用计划代替后续批准上限、持久范围日志、来源更新规则和真实自动UI验收。

行情/因子内部自动提交可携带由持久草案构建的ScheduleExecutionApproval。普通submit在重新规划后检查冻结选择、新增分段及当前无重试信用上界；未知估计拒绝，超限要求新批准，confirmed=true不绕过。通过该检查才可免去本次普通人工确认；硬规模、授权、数据库准入及出站账本仍生效。此参数不在公开HTTP请求中，手工提交行为不变。批准信用目前仅是规划估计上限，实际metadata/重试由共享硬预算控制；研究批准及超限暂停/timer/UI装配仍未完成，不能宣称完整自动采集已可用。

0015新增不可变collection_schedule_dispatch恢复日志。ScheduleDispatchDecision用明确market/research类型保存原请求、半开时间范围、run/correlation身份、草案revision及policy依据/首次freeze时间，无凭证字段。当前授权及有效lease下冻结；恢复先读取原日志，不能按重启时日期重算请求。重复同意图保留首次时间和摘要，任何范围/依据/来源变化冲突。market最终入队在原事务内核对日志与请求完全一致，缺失/摘要损坏/槽关联不一致拒绝。只保存日志不等于创建任务或下载成功；timer/来源资格和研究执行准入仍待装配。禁止SQL重写、删除或truncate日志代替重新计划。

`COLLECTOR_RUN_WORKER`、`COLLECTOR_RUN_SCHEDULER` 与 `COLLECTOR_RUN_FRESHNESS` 均只接受精确的 `true` / `false`；拼写、大小写或空白错误会在装配时拒绝，避免服务表面就绪但任务永久排队。显式关闭 Worker 时 `/ready` 不伪造 Worker 周期故障，也不代表下载能力已验收。

内部timer现已装配ScheduleRunner，`COLLECTOR_RUN_SCHEDULER=true`默认开启，`COLLECTOR_SCHEDULER_INTERVAL_SECONDS=2`（1–60秒）；与Worker/元数据任务分开，只领取enabled计划，创建仍默认disabled。每轮最多一计划/一槽，lease600秒，当前授权、读取/冻结请求、普通预览/批准/事务入队、推进游标；进程失联后租约重领并幂等恢复。明确撤权/来源失效/超批准暂停，预算拒绝/竞争延后；submitted日志不代表下载完成。启用内部控制还核对冻结主体权限，另一管理员不能替代。

当前日线策略复用Provider真实日历及文档预计完成时刻；相邻操作槽之间没有新预计发布就正常跳过，周频日线可在周日取得周五数据。市场本地日期计算lookback半开范围，不用本地已采日期决定截止。因子/财务/事件频率未知不套价格规则，仍不支持自动策略；启用HTTP/UI未提供，不因timer已部署宣称这些能力可用。暂停及等待的完整UI、实际重启/撤权矩阵继续按Goal完成。

正式自动更新入口位于数据采集工作台的独立panel，创建折叠区默认停用：明确标的优先，否则选择一个冻结股票池；设置北京时间、节奏/星期、回看/补跑、批准规模/信用并确认后保存，再核对下一槽并启用。仅开放已验证更新规则的日线；未知类别/来源策略拒绝。来源预计时刻不是完整覆盖证明，以最终任务/发布/终端为准。停用不取消已入队任务、不删除产物；修改冻结输入应停用并创建新计划。已暂停显示authorization/source_unavailable/approval_required原因，修复前不盲目启用。

公开collector GET/POST `/v1/schedules`，GET单计划，POST `/{id}/enable|disable`使用revision；分页limit1–100/offset0–1000000。QTrade会话桥接 `/data/collector/schedules` 只让DATA_MANAGE且CSRF有效者创建控制，READ可列出；actor/scope从会话派生，不能客户端注入。创建和启用均再次核对冻结执行主体、配置/市场/资产/日线策略及来源元数据权限，执行仍共享硬预算。只读UI隐藏写按钮；服务故障保留旧列表并提示，不把缓存显示当成当前状态。Cookie/CSRF不进入表单/日志。

性能诊断allowlist新增 `transport_ms`，来自有界HTTP子进程、Client构造后开始计时，包含网络等待/正文接收/少量文件及close开销；不含spawn、pacing、父进程解析/归档，也非供应商内部计算。成功及已完整返回的HTTP响应可计时；deadline杀进程或缺测保留未知，不补零。`duration_ms`和`pace_wait_ms`既有含义不变，HTTP字节与不可变产物不改。固定1/5/20完整2025日线的真实基线、账本/采样资源/对象和完成产物重启复验见 COLLECTION-PERFORMANCE-BASELINE-2026-10-14.md；不将该基线外推全市场或作为执行中/备份恢复验收。

研究转换错误：明确转换拒绝及类型校验ValueError产生versioned processing-failure，原始页已持久、标准数量未知；未知编程/存储异常只以unavailable记录运行失败，不能解释为来源无数据或已检查通过。修复原因后可从工作台手工重试，冻结输入/旧证据不改；仍从第一页执行且实际来源调用记账，该“重试当前任务”不承诺跳过已完成分页；完整RAW的独立离线重处理使用后文单独入口。写失败状态仍须有效租约，不能通过改表绕过。验证见RESEARCH-CONVERSION-RECOVERY-2026-10-14.md。

新鲜度快照查询：0016保存不可变checked_at历史，当前仅collector POST /v1/freshness/latest（严格FreshnessKey）与 /v1/freshness/history（key/limit1–100/before有时区）供已有服务身份和当前READ调用。body scope必须匹配X-Data-Scope；成功no-store，查询不探测来源/不花来源信用。无快照404/空历史，不等于健康；checked_at旧快照不能解释为实时current。没有公开写入接口，禁止改表/清空历史生成健康值。生产证据核验和业务UI已分阶段接通（见下文）；自动刷新及告警仍未装配，操作员不能据端点已部署宣称完整新鲜度监控已可用；证据见COLLECTION-FRESHNESS-STORE-2026-10-15.md。

QTrade已提供当前READ的GET /data/collector/publications/{id}/freshness：从scoped公开manifest推导数据集身份，读取collector保存快照，浏览器不提供actor/scope。采集日线结果按需显示记录，刷新只重新读取，不会探测来源或创建快照。API运行环境QTRADE_FRESHNESS_SNAPSHOT_MAX_AGE_SECONDS默认900，范围1–86400，修改后重启API；与来源更新宽限不同。过期/读失败/时钟异常显示待核验，保留历史记录；没有记录显示暂无检查，不能按下载完成推断fresh。管理员另可按下文主动重新核验，持续生产、独立最新行情探测和告警尚未接通。只读阶段验证见COLLECTION-FRESHNESS-UI-2026-10-15.md。

持续新鲜度生产器的提交协议为FreshnessRefresh.commit：只在来源/归档/策略验证后进入短key事务，当前manage再次核验，保留较强collected/published；来源故障与未知规则仍显式。superseded必须重新准备检查，不能给旧源观察换新时钟；replayed不重复插入。底层精确record/append只用于归档，不替代合并。PG连接5秒、锁等待默认5000ms（适配器可信参数1–30000ms），超时unavailable无部分写。当前仍无持续刷新/告警装配，不据内部提交模块就绪宣称监控交付。证据COLLECTION-FRESHNESS-MERGE-2026-10-15.md。


管理员主动核验：日线已完成结果提供“重新核验新鲜度”，通过QTrade POST `/data/collector/publications/{id}/freshness/check`（当前DATA_MANAGE及CSRF）调用collector POST `/v1/freshness/check`。后者body严格只有publication_id，使用受信身份和当前manage；禁止直接提交健康值、scope或时钟。核验读取来源真实日历和已有价格归档，不下载最新行情；元数据访问共享调用账本/缓存，可能消耗额度。旧历史不能证明来源今日可用，unknown是合法结果，需要新行情时另行预览补采。返回存储结果，刷新快照仍只是读；失败显示待核验，不继续展示旧健康。

当日完整周期要求所选raw行在完整预计发布时间后由真实history回执取得，缓存重读不能作为获取时间证明；旧周期保守要求次日取得。仅当前CN未复权日线支持主动核验，不将日线规则外推到稀疏财务或因子。归档损坏或证据不足返回unverified，来源元数据不可用/预算不足明确拒绝，保留旧不可变历史；并发superseded或规则跨界最多两次重准备，持续冲突返回conflict。自动刷新/outbox/告警和完整其他类别规则仍待交付，见COLLECTION-FRESHNESS-CHECK-2026-10-15.md。


新鲜度事件outbox：0017追加不可变collection_freshness_notice，FreshnessRefresh在同一key事务内保存核验和事件。持续late/unavailable仅开启一次；unknown/grace及来源未知的current不恢复，current且source_health=available才追加同episode恢复。普通历史append/record不发运维事件，事件写入失败回滚快照。scope、完整key、UTC开启时刻形成稳定episode/notice身份；关联snapshot及内容摘要读时核对，禁止改写/删除/清空事实。当前已接公开租约领取/确认和站内通知消费（见下文），持久自动核验尚未接通；不能把有outbox记录解释为通知成功。后续投递应按同key顺序、幂等持久化通知后再确认。见COLLECTION-FRESHNESS-NOTICE-2026-10-15.md及受控/真实部署分开的证据。


新鲜度事件投递：0018追加delivery状态，不改0017事实。collector POST /v1/freshness/notices/claim要求service token、严格consumer_id/limit1–100；consumer须匹配COLLECTOR_FRESHNESS_CONSUMER_ID默认qtrade-alerts。60秒租约，停机后到期重领，同key按事件顺序确认；旧token不能确认新租约。POST /v1/freshness/notices/{id}/ack另需原actor/scope当前manage及正确consumer/token，同已接受token重复确认幂等。全局claim不通过浏览器代理提供。

QTrade业务Worker独立循环消费，以稳定ID保存既有告警事件和站内通知投递队列后确认。确认代表持久接收，须再查通知Worker最终in_app落库/attempt delivered才能声称送达；不会自动关闭人工告警。两端consumer配置须一致，Compose及样例已给默认。当前受控producer真实投递两条并刷新/UI通过，非供应商真实异常，仍未完成自动监控；见COLLECTION-FRESHNESS-DELIVERY-2026-10-16.md。自测命令scripts/selftest-freshness.py使用本地15436并仅创建/删除自有临时库，生产库不执行恢复或清理。


后台证据核验：日线结果中的折叠区可先“查看后台核验”，管理员“配置每15分钟核验”只创建停用配置，再明确“以我身份启用”。启用采用当前操作者权限并记revision，停用保留已保存检查/错误/告警。当前UI默认900秒，公开FreshnessWatchCreate支持60–86400秒，重复创建不覆盖旧配置；不是新的行情下载计划。全局COLLECTOR_RUN_FRESHNESS默认true，仅true/false，关闭不删除配置。持久watch/不可变配置日志由0019追加迁移保存。

独立后台每5秒领取一份应执行watch，600秒租约及token/revision/主体/口径fencing；出站前和提交时重新检查当前manage。暂停/未知/用量拒绝有明确状态，来源访问共用既有限流与预算。停用后旧执行不能提交；进程丢失后最迟原租约到期可重领，核验可能新增历史记录但稳定incident不重复通知。公开/v1/freshness/watches创建/按发布读取/按ID读取/版本控制；QTrade会话桥接管理操作带CSRF，浏览器不能注入actor/scope/due/lease。

较新后台失败会让旧健康显示待核验，即使年龄TTL未到；配置操作不清掉失败或更新失败时钟，新的人工成功可取代旧失败。后台配置启用不等于来源或数据健康，不会下载最新价格；已发布旧历史仍可能unknown。当前作用域只读总览见下文；其他类别自动策略、跨工作区watch健康/积压面板及真实来源异常到通知恢复待完成。证据及复现命令见COLLECTION-FRESHNESS-WATCH-2026-10-16.md。

当前工作区的“新鲜度健康”折叠区提供只读总览，覆盖已有核验记录或watch配置的数据口径；未列出的数据未经验证。页面每页20项，可前后翻页并刷新第一页，不创建检查或下载。每项展示简称/代码、来源/周期/映射、截至与预期、后台问题及最近事件接收状态，详情显示采集/发布/可获取时间和告警入口。消费端确认是持久接收，不代表最终通知送达；最终渠道须到告警/通知记录核验。

公开collector POST `/v1/freshness/health`使用严格FreshnessHealthQuery，当前READ和服务身份；QTrade GET `/data/collector/freshness/health`从会话推导scope/actor，仅接受limit1–100及after_key完整摘要游标。每页一致只读事务，不保证跨页冻结快照；新增记录后可刷新重读。全局来源/其他工作区库存不向浏览器开放。COLLECTOR_FRESHNESS_HEALTH_MAX_AGE_SECONDS默认900、范围1–86400，控制保存检查的展示有效期，不改变来源发布时间或后台周期。超龄、未来时钟、较新失败、未注册/未配置版本均不显示为当前健康；读取失败保留旧证据但待核验。此总览不是全量数据覆盖证明，也未补齐最新来源观测及其他类别规则。

“来源能力与证据”默认折叠，按需读取collector GET `/v1/source-profiles`（QTrade会话桥接 `/data/collector/source-profiles`），以及参考侧 GET `/data/universes/source-profiles`。来源选择仅查看说明，不发起预览或下载。每项展示八个方面的有界事实、证据等级/核对日期/文档引用；未知项说明最小确认条件。当前有七项已注册采集类别、当前成员快照及三项未接通扩展要求共11项；未配置与未接通分别呈现，不能从付费证书推导全部权限、配额或对外分发许可。当前成员只代表观察时刻名单，观察时间非历史生效区间，未提供权重或历史日期查询。

两个目录各自拥有本部署配置与当前READ授权，不相互查询供应商；工作台组合时检查作用域和来源身份唯一。单侧不可用时显示缺失分组，仅展示实际读到的部分；两侧均失败时旧说明只供参考。恢复后“刷新来源说明”重新读取，不改变已有计划或产物。当前源注册表只有MaiRui；RQData/Tushare等未注册本轮实际采集，不能把已有代码或文档当已接通。

新增来源/类别须同时提供SourceProfile与原有采集/研究能力契约：逐项填写历史边界、原始/标准字段单位、调整与版本、时间轴/端点、更新、权限、配额、用途许可；未知保持unknown并写明unblock_condition。证据引用使用仓库docs内已有脱敏报告，不能放含证书URL；e2e_verified仅对报告中的明确场景有效，不外推所有证券/周期。配置状态来自本地适配器，禁止页面读取时出站探测。样例见`qtrade_data_mairui/source_profiles.py`及独立核心SourceProfileDirectory，三个独立JSON Schema在data-contracts/schemas/v1；不要为补充说明修改冻结ResearchPlan中的capability默认值、成员快照或控制摘要。新部署可承载相同公开契约，消费页面不用导入来源实现或业务数据库。

研究响应回执：财务/事件任务行“查看来源响应回执”打开独立只读对话框，展示已记录HTTP状态、字节数、时间/映射与摘要，支持最新优先分页、刷新、键盘及Escape关闭。HTTP 200或完整正文不等于数据通过/已发布，旧任务无回执不等于没有下载。接口为collector `/v2/research/jobs/{id}/response-receipts?limit=20&after_id=...`（当前READ、同任务游标、最多100），QTrade会话桥接相同尾路径并严格复核/no-store。无正文浏览器端点，也不因读取自动请求供应商。

0020保存不可变响应元数据，正文在原研究归档私有目录下先fsync；权限及备份遵循原归档策略，不能把这些私有响应作为公开文件服务。新适配器需要解码前留存时实现同步fetch_with_capture，窗口不能保留、缺少捕获/写入失败不能放行；原非HTTP RAW接口不因此变成HTTP证据。完整契约、部署/故障/真实下载验证及未完成项见[研究响应回执](../testing/RESEARCH-HTTP-RECEIPTS-2026-10-16.md)。

研究响应的传输失败：总期限后先kill/join socket owner再归档已收到的有界字节；截断、超限、仅响应头均明确complete=false，不继续解析发布。超限字节数是保留前缀量，不能作为全部网络流量。无响应的连接失败无回执；强杀Worker/宿主仍可能只留私有临时目录，不能保证已fsync归档或声称可离线重处理。没有新增配置/迁移/公开正文端点，仍由现有预算记失败尝试；存储失败不会返还已发生请求的信用。见[部分响应验收](../testing/RESEARCH-PARTIAL-RESPONSES-2026-10-16.md)。

## 研究 RAW 离线重处理

财务与事件近期任务的“RAW 重处理”打开独立对话框，先“预览当前 RAW”，检查父任务、范围、转换、输入页/行、上限，再确认创建独立任务；提交接受不等于发布。刷新处理结果或关闭后刷新任务列表，再从新任务浏览已发布版本。重处理不重新下载、不更改旧任务/旧引用，继续经过原发布门禁；原采集时刻及未知可知时间保持，不产生 PIT 证据。

仅完整连接的RAW分页链可用；只有HTTP响应回执、缺页、摘要损坏或不兼容转换不能重处理。先解决对应输入/转换部署问题，不能靠此按钮补下载缺页。前端不提供私有路径或正文。只读用户可读已保存的重处理血缘，不能预览/提交。

服务端可设置 `COLLECTOR_RESEARCH_REPLAY_MAX_PAGES`（默认/最高1000）、`COLLECTOR_RESEARCH_REPLAY_MAX_ROWS`（默认/最高100000）和 `COLLECTOR_RESEARCH_REPLAY_MAX_BYTES`（默认/最高67108864）。均为正整数、不可超过契约最高值；字节核验同时包含压缩文件与Parquet解压预算，不是HTTP流量额度。执行再次使用冻结/当前限制中更严格者。修改配置需重启collector，不改写既有意图。0021追加不可变意图/输入表，沿正常迁移部署，不修改既有迁移；回滚保留新增表/归档，旧Worker对离线意图必须失败关闭。

提交超时后使用同一预览重试，服务幂等返回同一任务；不要为了重试先生成另一份预览。明确重新预览会生成新的候选任务身份。业务错误使用固定安全码，日志按scope/request定位，切勿粘贴证书或私有响应正文。

HTTP接口：collector使用 `/v2/research/replays/preview`（ResearchReplayRequest）、`/v2/research/replays`（ResearchReplaySubmission）及GET `/v2/research/replays/{request_id}`（ResearchReplay）。JSON Schema位于 `packages/data-contracts/schemas/v2/ResearchReplay*.schema.json`。QTrade对应工作区路径为 `data/collector/research/replays/preview`、`.../replays`及`.../replays/{request_id}`；会话层预览仅接收request_id/parent_request_id，提交另加control_sha256，不接受actor/scope或上限覆盖。

消费者读取推荐 `POST data/collector/research/records`，提交publication_id/content_version/kind及可选cursor/limit/as_of，主体和工作区从当前READ会话推导；返回严格ResearchRecordPage。旧 `research/query`显式身份契约保留且仍拒绝冒用其他主体；不要把任务创建者身份复制成查看者身份。as_of继续由来源已知证据和原研究服务校验，普通历史财务仍拒绝PIT查询。

## 行情/因子转换输入检查点

新MaiRui任务在转换前将完整获取区间RAW、实际日历/因子定义及源值类型写入私有`scope/part/normalization-inputs/input-<UUID>.json`。这与原获取回执、标准数据及发布清单分开：存在检查点不表示门禁通过/发布，准备时刻不是来源更新时间或历史可知时间；旧任务无检查点也不等于无采集。备份归档卷时须包含这些新增文件。

检查点采用v2严格契约`CollectionNormalizationInput`，Schema见`packages/data-contracts/schemas/v2/CollectionNormalizationInput.schema.json`。同身份幂等保留首次时间，异常输入/摘要/作用域/写入失败阻止后续转换。单输入128MiB，来源正文核验256MiB、回执元数据64KiB；一次最多枚举100份且输入总计128MiB，行情输入最多100000行、因子仍365行；不静默截断，超限必须检查来源/分段与配置，不能编辑原文件。源值类型额外保留是为区分Decimal精确值与数值形文本信号，未知单位/定义/可知时间保持未知。

当前只有独立纯转换接口和私有输入基础，**行情/因子公开离线重处理任务/UI尚未接通**；“重试分段”继续原获取路径，会消耗实际来源调用。不能把直接修改检查点、直接写发布表或受控转换调用当成操作入口。完整研究RAW的现有重处理入口另见前文，不适用于行情/因子。核验命令和证据见[输入检查点验收](../testing/COLLECTION-NORMALIZATION-INPUT-2026-10-17.md)。

## 行情与因子离线重处理

在任务分段点击“离线重处理”，明确选择完整转换输入，预览当前注册转换/规则、输入SHA、原获取时刻、范围和资源上限，再确认创建独立任务。行情与因子沿用原Worker队列、租约、质量门禁、发布及消费者导入；原任务、失败证据和已有研究引用不变。只读成员可查看输入与保存的血缘，没有预览/提交管理权限。只保留HTTP字节、缺少完整转换检查点的旧任务不能执行此入口；研究RAW入口仍单独使用。

独立服务公开GET `/v2/collection/jobs/{job_id}/parts/{part_id}/normalization-inputs`、POST `/v2/collection/replays/preview`、POST `/v2/collection/replays`及GET `/v2/collection/replays/{request_id}`。QTrade通过当前工作区会话桥接；POST需要当前DATA_MANAGE和CSRF，读取需要READ。公开契约/JSON Schema在`packages/data-contracts/schemas/v2/CollectionReplay*.schema.json`及`CollectionInputReference.schema.json`；端口及私有归档实现独立，不将正文、租约令牌或证书返回浏览器。

| 配置 | 默认 | 用途 |
| --- | ---: | --- |
| COLLECTOR_NORMALIZATION_REPLAY_MAX_ROWS | 100000 | 输入和转换输出行数上限 |
| COLLECTOR_NORMALIZATION_REPLAY_MAX_BYTES | 134217728 | 检查点、选定回执元数据及正文合计字节上限 |
| COLLECTOR_NORMALIZATION_REPLAY_MAX_RECEIPTS | 4096 | 选定原获取回执数量上限 |

配置必须为正整数且不超过契约硬上限；非法配置拒绝启动，不回显配置内容。预览和执行均检查，执行采用冻结及当前上限的较严格者；收紧后仍可容纳原输入不因配置数值不同自动拒绝。转换/规则/输入变化则需重新预览，不能静默改用新承诺。预览不入队，重复request/control返回同任务；提交超时先查询原编号或以同一预览重试，重新预览是新的候选身份。关闭窗口不取消任务，取消和失败重试仍由任务控制提供。

新发布的`normalization_replay`证据记录原检查点、原始回执时刻和规则；不会在子任务目录复制或伪造获取回执。日线新鲜度核验跟随冻结根输入的真实history行证据，新发布时间不能作为最新采集。缓存重读回执不证明原获取时刻，未知仍显示未知；离线操作不会补出PIT、历史成分或复权能力。发生损坏、权限撤销、缺意图或不兼容转换时明确拒绝，不回网下载替代数据。

0022是追加迁移，元数据不可更新、删除或truncate。队列payload保留离线标记并使用v2请求封套，旧v1解码器会拒绝而不是当作普通下载；无重处理器的新Worker也拒绝。升级先部署兼容仓储/Worker及迁移，再开放入口。回滚应用必须保留0022迁移识别和v2队列解码能力；不能删迁移账本或手动移除离线标记。需暂停或取消未执行任务时，在兼容版本通过既有任务控制完成，不用旧Worker强行消费。
