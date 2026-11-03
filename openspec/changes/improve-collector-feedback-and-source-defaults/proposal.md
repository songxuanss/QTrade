# Proposal

## Why

真实用户实测（体验缺陷清单第 1/2/3/5/6 条）暴露一条共同故障链：本机 WSL2 mirrored 网络封锁直接 TLS 出口，独立采集服务（host 网络模式、未注入代理）对 MaiRui 的全部 provider 请求超时。表现为「使用最近自然年」「预览独立采集计划」点击后长时间无反应、数据管理页被逐个 5 秒超时的元数据刷新拖慢（warm DCL 8.4s / cold 15.3s）、任务失败只见「来源请求超时，任务会按退避计划重试。」而无法归因。修复出口后仍遗留两类体验缺陷：操作反馈渲染在面板顶部视口外（失败原因不可见）、来源默认选择永远落在免费源，与「优先可用付费源、不可用才回退免费源」的产品预期相反。

## What Changes

- 部署配置：`compose.collector.yaml` 为 collector 服务透传标准代理环境变量（`HTTP_PROXY/HTTPS_PROXY/NO_PROXY`，由 `COLLECTOR_HTTP_PROXY/COLLECTOR_NO_PROXY` 注入，默认空 = 直连，不影响其他部署），构建期同样以预定义 build args 透传，保证封锁直连出口的主机上 pip 可完成镜像构建；`NO_PROXY` 默认含回环地址，健康检查与内部 authorizer 调用不经外部代理。本地代理地址只写入被 gitignore 的 `.env.collector`。
- 数据契约：`SourceDataset` 新增 `tier: "paid" | "free"`（默认 `"free"`，向后兼容）；MaiRui adapter 将 Pro 分钟历史与因子历史标记为 paid，普通历史标记为 free。业务 API 为透传，无需改动。
- Web 采集工作台：默认来源在 configured 的支持当前类别与周期的来源中优先付费源，无付费可用才回退免费源；用户保存偏好中的显式来源选择不被覆盖；切换周期时若当前来源不支持新周期，自动切换到支持它的最优（付费优先）来源；来源下拉标注付费/免费。
- Web 反馈可见性：表单类错误（预览、默认区间、保存选择、提交）在触发按钮所在表单页脚就近内联展示，顶部横幅保留；错误出现时自动滚动进入视口；「使用最近自然年日期」等待期间显示「计算中…」，禁用态按钮旁展示禁用原因（已有日期 / 来源未配置）。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `data-collection-workbench`: 新增来源默认优先级与操作反馈就近可见两组要求（该能力规格由未归档变更 `redesign-data-collection-workbench` 创建，本变更以 ADDED 要求叠加）。

## Impact

- 部署：`compose.collector.yaml`（代理透传）、`.env.collector`（本地私有，不入库）。
- 契约/适配器：`packages/data-contracts/.../collection.py`、`adapters/data_mairui/.../collection.py`。
- 前端：`apps/web/src/features/data/collector-types.ts`、`collector-panel.tsx`、相关单元与验证脚本。
- 不改：任务状态机、权限、计划/提交契约、数据库、行情数据、研究（财务/事件）单来源目录。

## Non-Goals and Safety Boundaries

- 不做运行时跨来源自动回退采集：任务绑定单一来源，付费源故障时由用户显式改选免费源（自动回退涉及覆盖/映射版本语义，留给架构文档评估）。
- 不改采集计划、分段、预算与重试语义。
- 不在仓库内固化任何环境私有代理地址或凭据（A6）；compose 默认值为空，行为等价现状。
- 不引入新第三方依赖（A12）。

## CONSTITUTION 条目

- A1 规格先行：本变更即 openspec delta，行为改动均落在规格要求上。
- A2/A3 端口与适配器边界：tier 是厂商中立的契约字段；「哪个源是付费」的厂商知识只写在 MaiRui adapter。
- A6 凭据不入库不入日志：代理 URL 无凭据且只进 gitignored env；NO_PROXY 保证内部回环调用不经外部代理，避免内部 URL 泄露给代理。
- A9 外部故障显式披露：上游超时从「无反应/视口外横幅」变为就近、可归因的明确报错。
- A11 时间与交易日口径：默认区间仍走来源交易日历，语义不变。
- A12 依赖锁定：无新依赖。

## Unresolved Assumptions

- 本地代理 `127.0.0.1:10780` 由用户 Windows 侧提供且长期可用；生产环境采集服务器出口策略（直连/代理/专线）在架构设计文档（后续变更）中单独决策。
- 付费源「可使用」当前以 `configured`（licence+Pro 开关）为准；上游实时健康探测不纳入本次范围。
