# 独立采集与市场消费接口 v1

OpenSpec：`extract-independent-data-collection`。架构决策：[`0007`](../adr/0007-independent-collection-publications.md)。

采集服务负责来源接入、下载计划、后台执行、归档与标准产物；市场终端属于 QTrade 数据消费业务。双方通过 HTTP 协作，不共享任务表、业务数据库或归档路径。`apps/collector` 镜像只安装 `qtrade_data_*` 包，不安装策略、账户、交易、QTrade API/Worker 包。旧采集任务及股票池 campaign 继续由兼容 Worker 处理，不被新服务重置或迁移。

## 资产与名称

身份包含资产、市场、交易场所、产品代码。保留 `601288.SH`、`index:000001.SH` 兼容；扩展使用 `stock:HK:XHKG:00700`、`stock:US:XNAS:AAPL`、`etf:CN:XSHG:510300`、`future:CN:CFFEX:IF2612`、`future:CN:CFFEX:IF@CONT`、`crypto:GLOBAL:BINANCE:BTC-USDT@PERP` 等。日期合约、连续合约、现货、永续必须使用不同身份。货币、交易时段、最小交易单位由后续参考数据/来源适配提供，不从代码猜测。当前 MaiRui Adapter 仅声明 CN 股票和指数能力；其他市场/品种可由契约表达，但在创建任务前拒绝。

名称从供应商参考目录读取，预览、任务和产物冻结简称快照。未提供名称时显示“名称未核验”，不生成假名称。任务及图表同时显示简称与代码；改名不会重写旧产物。

## HTTP 规约

所有业务调用使用 `Authorization: Bearer <service-token>`，主体请求另带 `X-Data-Actor`、`X-Data-Scope` UUID。部署默认使用 HTTPS；本地验证需显式打开 HTTP。服务令牌代表调用方，不能代替主体的数据权限。

| 接口 | 语义 |
| --- | --- |
| `GET /v1/catalog` | 来源、数据类别、市场/资产、周期、时区、映射版本、配置/证据和限制 |
| `GET /v1/factors` | 当前因子目录，定义、版本、单位及不可用原因 |
| `GET /v1/usage` | 当前READ作用域的当日内部信用预算、结果及拒绝详情；共享源只返回汇总 |
| `GET /v1/names?instrument_id=…` | 标的简称解析（最多 200 个） |
| `POST /v1/preview` | 冻结请求、补缺/全量预览及覆盖版本 |
| `POST /v1/jobs` | `{request, coverage_version}`，按请求 UUID 幂等提交 |
| `GET /v1/jobs` | 最近任务 |
| `GET /v1/jobs/{id}?offset=0` | 任务进度、异常、名称和分段（每页最多 100） |
| `POST /v1/jobs/{id}/{retry\|cancel}` | 重试失败/空分段或取消；共享分段仍为其他任务继续 |
| `GET /v1/publications/{id}` | 不可变产物清单 |
| `GET /v1/publications/{id}/records?offset=0&limit=1000` | 标准记录、页摘要和下一 offset |
| `GET /v1/deliveries?consumer_id=qtrade-market` | 服务身份订阅待消费行情清单；最多 50 |
| `POST /v1/deliveries/{id}/ack?consumer_id=qtrade-market` | 主体 manage 授权后确认消费 |

契约源位于 `packages/data-contracts/src/qtrade_data_contracts/collection.py`，JSON Schema 位于 `packages/data-contracts/schemas/v1`。仅接受 `schema_version=1`，未知字段/版本拒绝；日期必须含时区。请求范围为 UTC 半开区间 `[start,end)`，界面日期先按来源时区转换，结束日期包含当天。

价格、成交量、金额、数值因子以精确 Decimal 文本序列化。MaiRui 股票分钟标签为北京时间区间结束，标准 Bar 时间为区间开始；成交量由手转换为股，金额为元，并记录倍率。指数价格为指数点。因子是独立模型：`factor_id/trade_date/value/value_kind/missing_reason/calc_version/available_at/availability_evidence`。空值保留原因，不替换为零；信号文本与数值分开。来源没有给历史可知时间或计算版本时保留 unknown，不声明 PIT 回测就绪。

清单包括 scope、产物/任务/分段/关联身份、来源、简称、范围、字段、映射版本、原始 Parquet SHA-256、标准记录摘要及数量、单位、时间口径、实际覆盖证据和限制。原始 Parquet 摘要计算文件字节；标准记录和分页摘要计算 `sort_keys=True,separators=(',',':'),ensure_ascii=False` 的 JSON UTF-8 字节。

## 持久执行与恢复

独立数据库只使用 `qtrade_data` schema。scope 事务锁防止预览提交竞争；实际提交重新验证覆盖版本。补缺按来源、类别、标的、字段、周期、映射版本和未复权语义隔离；全量重采生成新产物，不覆盖旧引用。总日期范围默认不限；新计划按来源/类别策略分段，Pro分钟采用市场年度边界，普通分钟保持七天保守分片，可配置进一步收紧。Pro较早年份按年拉取后裁剪并复用年片；旧冻结任务分段及引用保持原样。

分段通过 SKIP LOCKED 领取，租约十分钟、独立 fencing token；旧租约不能提交覆盖新租约。限流/超时/暂时不可用最多八次退避重试，失败可手工重试。归档先写不可变原始和标准 Parquet，最后提交 manifest，数据库随后标完成；重启可恢复已归档产物。无观测记录是 no_data，不是采集完成；仅因子字段为空仍保留真实交易日观测及空值证据。

## 授权和消费可见性

QTrade `/internal/data/authorize` 以同一服务令牌验证请求，再回查当前 workspace membership/read/manage 权限。API 操作、独立 Worker 和消费者都复核权限，撤权后不得继续采集/导入；回查不可用按暂时失败处理。其他调用方可提供自己的授权 Adapter，或显式配置 actor:scope→read/manage 静态许可。

QTrade Worker 只读清单和标准分页，校验 scope、身份、周期、范围、每页/整体摘要和记录数。原始供应商归档留采集服务；QTrade 保存标准 Bar 导入副本及含原始摘要的质量报告。先写行情候选再提交数据集 metadata；市场读取只选择已发布数据集，半导入不对终端可见。产物身份是幂等键，元数据成功后才确认订阅，失败继续重试。

界面区分采集完成、导入等待、导入失败、终端可见。因子可直接分页浏览，不导入价格 K 线。未来新增供应商只需要实现 CollectionProvider；新增消费者实现 HTTP 产物消费，无需读取 collector 私表。
