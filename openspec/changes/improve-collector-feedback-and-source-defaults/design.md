# Design

## 故障链诊断（证据）

- 宿主 WSL2 为 mirrored 网络：`curl https://api.mairuiapi.com/` 直连成功（HTTP 400 为无 licence 路径的预期响应），但容器内与宿主 python `ssl` 直接握手全部超时（TCP 可连、TLS 卡死）；经 `127.0.0.1:10780` CONNECT 隧道 TLS 1.5s 成功。
- collector 以 `network_mode: host`（compose.collector.local.yaml）运行，容器内无任何 proxy env；`httpx` 客户端未显式关闭 `trust_env`（默认 True），因此注入标准代理环境变量即可生效，无需改 adapter 代码。
- API 侧观测：`GET /data/collector/default-range` 5.3s → 503；`/names` 5.35s、`/jobs` 4.56s、`/research/catalog` 3.7s——数据页 warm DCL 8.4s 的主因。collector 日志每分钟 `provider_request timeout`（duration≈5.2-6.2s, response_bytes=0）。
- 修复后复测：自然年按钮 5.1s 填充 `[2025-11-04, 2026-11-03]`；预览 5.2s 出计划；warm DCL 3.8s / idle 4.3s。

## 决策

1. **代理透传放 compose 层而非代码层**：httpx/urllib 均尊重标准 proxy env（trust_env 默认开），显式代码传参反而把环境拓扑耦合进 adapter。`COLLECTOR_HTTP_PROXY` 默认空 = 直连，等价现状，可逆。构建期同理：pip 在 `network: host` 构建中同样被封锁直连出口，故 compose build args 也透传同名变量（Docker 预定义 build args，无需改 Dockerfile），默认空不影响正常构建。
2. **NO_PROXY 默认回环**：healthcheck（urllib → 127.0.0.1:18100/ready）与 authorizer 回调（httpx → 127.0.0.1:3122）必须绕过代理，否则内部 URL 泄露给外部代理且引入故障点。
3. **tier 进契约而非 web 硬编码命名约定**：`mairui.pro.*` 字符串匹配会把厂商知识泄露进 UI；`SourceDataset.tier` 是厂商中立字段，默认 `"free"` 向后兼容，strict/frozen/extra=forbid 不受影响（新字段带默认值）。运行时跨源自动回退不做：任务绑定单一来源，回退会改变覆盖与 mapping_version 语义，属加法，留给架构文档。
4. **反馈双通道**：顶部横幅服务全局/结果区错误，表单页脚内联服务创建流错误；错误变更时 `scrollIntoView` 保证可见。不拆 error state，两处以同一 state 渲染，避免状态漂移。
5. **周期驱动的自动换源**：period 变化且当前源不支持时选「configured ∧ 支持周期 ∧ 付费优先」的最优源；用户手动选源（sourceId 显式设置）后不再自动覆盖，保存偏好恢复同理。

## 失败模式

- 代理不可用：provider 请求超时 → 就近显示「来源请求超时…」，任务按既有退避重试；不会静默。
- 付费源 licence 失效：configured=false → 默认逻辑自动落免费源；下拉显示「未配置」禁用项。
- 保存偏好指向已下线来源：既有「已保存来源不可用，请重新选择」占位保留，默认逻辑不强行覆盖用户显式选择。
