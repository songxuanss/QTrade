# Adapters

外部引擎、行情、Broker、研究、通知与对象存储的实现放在此目录。
`mock/src/qtrade_mock` 是测试 Adapter 的空模块入口，后续按任务 2.2 实现。
生产 Adapter 与测试 Adapter 通过领域 Port 接入，由应用入口组装；
API 与领域包不得直接导入外部 SDK。LEAN 等引擎在隔离 runner 中运行。
当前已接入 MaiRui A 股历史逐笔数据 Adapter；尚未接入真实交易通道。其他行情、Broker、研究和
通知仍使用 Port 或 Mock Adapter。

## Port 边界

`qtrade_domain.ports` 定义六个可结构化替换的同步 Protocol；业务调用必须携带 Workspace，
供应商异常统一转换为 `PortError`，仅瞬态错误声明可重试。同步调用由 application worker
调度并设置截止时间，不能把计算放到 API 请求中。`qtrade_mock.ports` 提供独立的可控测试替身：
显式完成计算任务，覆盖取消、幂等、不可变写入、校验和及作用域错误，不冒充真实回测或撮合。
后续真实 Adapter 要运行相同契约测试并补充供应商集成测试。

`qtrade_object_store.S3ObjectStore` 使用 S3 `If-None-Match: *` 原子创建，Workspace 前缀隔离，
上传 SHA-256 校验、下载重新计算内容哈希；同内容重试读取校验已有对象，不允许覆盖。
SDK 错误转换为安全 `PortError`，TLS 默认开启，HTTP 必须显式用于本地测试。
桶和凭据由部署配置提供，Port 不创建桶/删除对象。生产需 IAM 限制权限、拒绝非条件写入，
并按保留要求配置版本/对象锁；应用级不可变性不保证持有管理员凭据的人不能修改存储。

## A-share historical ticks

`qtrade_mairui.MaiRuiAShareTickProvider` is the first provider adapter. It uses MaiRui's
day-archived A-share trade endpoint and preserves the provider rows as immutable raw JSON
records. The collector pages through a bounded request range using durable day/row cursors,
filters timestamps in `Asia/Shanghai`, and leaves the provider SDK/HTTP format behind the
`HistoricalProviderPort` seam. Supported identifiers are six-digit symbols with an optional
`.SH`, `.SZ`, or `.BJ` suffix.

The archive begins on 2026-08-13, and MaiRui requires a licence (its documentation offers
licence registration and describes tiered rate limits). Configure `MAIRUI_LICENCE` for both
API and worker processes; the UI selects source `mairui.a-share.tick` and licence identity
`mairui-default`. The worker runs collection tasks durably from PostgreSQL. Local Compose
creates the `qtrade` object-store bucket automatically when this provider is enabled.

This adapter does not claim Hong Kong historical tick coverage. No free Hong Kong source with
user-selected historical tick ranges was verified, so Hong Kong requests are rejected rather
than silently downgraded to bars or latest quotes. Add a separately licensed HKEX or other
provider behind `HistoricalProviderPort` when that market's tick entitlement is available.

运行 `.venv/bin/python scripts/selftest-objectstore.py` 可对启动的 Compose MinIO 验证
Mock/S3 同一契约、跨 Workspace、错误、恶意校验和及八个并发创建仅一个成功。
测试创建独立随机桶并在 finally 清理，不修改现有业务对象。
