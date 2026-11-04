# Proposal

## Why

真实用户实测(体验缺陷清单第 7/10-14 条)暴露数据管理页信息架构断裂:顶层七个分区(行情与因子/板块与股票池/财务与事件/自动更新/数据资产/运行监控/逐笔与兼容)按实现历史堆叠,而不是按用户任务流组织;「圈定范围 → 选择维度 → 预览提交 → 追踪结果 → 查看来源健康」的主线被切碎在多个平级 tab 与两层嵌套 tab 中;独立采集面板单文件 1554 行,创建半与结果半共享一个内部视图开关,顶层 tab 无法把「多维下载」与「任务与结果」分成独立视图而不丢失草稿或轮询。三方会诊(IA/UX、前端成本、能力边界审计)产出 v2 原型并经用户批准,裁决为四视图 IA + 分阶段实施(B1 结构与拆分,B2 UX 增强,B3 多维编排)。

## What Changes

- 顶层信息架构:数据管理页由七个平级分区改为四个任务流视图——01 数据目录(板块/股票池圈定)、02 多维下载(采集配置+计划预览,含财务/分红/逐笔兼容入口)、03 任务与结果(采集任务详情+数据资产)、04 来源与新鲜度(新鲜度/用量/来源能力/计划调度)。
- 独立采集面板拆分:`collector-panel.tsx` 抽出共享标签常量(`collector-labels.ts`)与三个呈现组件(创建表单/计划预览/任务详情);`CollectorPanel` 保留全部状态与副作用,新增 children-as-function 组合模式,使同一实例的创建半与结果半分别挂载在视图 02 与视图 03——切换视图不丢草稿、任务轮询不中断;无 children 时保持现有合并视图渲染(测试与向后兼容)。
- 深链接兼容:`data_view` 旧值(collect/pools/fundamentals/schedules/assets/monitor/ticks)与 `collect_view`/`collector_job` 旧参数经别名映射落到新视图;fragment 锚点(independent-collector/financial-collection/legacy-downloads/collection/batches/quality 等)全部保留;SSO returnTo 白名单键不变;server action 重定向改发新视图值。
- 视图内导航事件统一为 `workbench-navigation`,顶层 tab 与采集面板双向同步(选任务自动切到视图 03,切 tab 同步内部视图态)。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `data-collection-workbench`: 顶层分区改为四视图任务流 IA,采集创建与结果跨视图单实例(该能力规格由未归档变更 `redesign-data-collection-workbench` 创建,本变更以 ADDED 要求叠加,其「采集工作流分区」要求由本变更的四视图要求承接演进)。

## Impact

- 前端:`apps/web/src/features/data/collector-panel.tsx`(拆分)、新增 `collector-labels.ts`/`collector-create-form.tsx`/`collector-plan-preview.tsx`/`collector-job-detail.tsx`/`data-workbench-views.tsx`、`workbench-tabs.tsx`(别名与导航事件)、`apps/web/src/app/workspace/data-workbench.tsx`(四视图装配)、`[section]/page.tsx`(初始视图归一化、collector 数据下传)、`data-actions.ts`(重定向视图值)。
- 测试:`collector-panel.test.tsx` 12 用例必须不改而全绿(合并模式 DOM 不变);`data-action-navigation.test.ts` 更新为新视图值;新增拆分模式与别名兼容用例。
- 不改:采集/预览/提交契约、任务状态机、权限模型、后端 API、数据库;财务与分红面板、股票池面板内部结构(B2 再强化)。

## Non-Goals and Safety Boundaries

- 不做 v2 原型的 UX 增强(全局范围条、继承态分组、抽屉清单、移动端 sticky 已选栏)——B2。
- 不做多维编排(维度卡片、循环预览、顺序提交)与 preferences schema 升版——B3。
- 不做覆盖率角标、跨域统一任务队列、run-now/暂停/ETA 遥测——会诊裁决 P3 不做(无后端支撑,不伪造能力)。
- 不新建后端服务或 API;所有视图能力来自既有 panel。

## CONSTITUTION 条目

- A1 规格先行:本变更即 openspec delta。
- A9 外部故障显式披露:错误双通道(页脚内联/结果横幅)语义在拆分后保持不变。
- 不伪造能力:四视图仅重组既有真实能力,未接入项(历史成分/退市)不被包装成可用。

## Unresolved Assumptions

- 旧深链接(书签/浏览器历史)以别名映射兼容一个发布周期;新链接一律使用新视图值。
