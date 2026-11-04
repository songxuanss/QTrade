# Design

## 1. 单实例跨视图:children-as-function

`DataWorkbench`(server)不能把函数 prop 传过 RSC 边界,而顶层 tab 装配必须在客户端完成(创建半与结果半来自同一个 `CollectorPanel` 实例的 state)。因此:

- 新增客户端组件 `data-workbench-views.tsx`(`DataWorkbenchViews`),在其中实例化 `<CollectorPanel {...collectorProps}>{(api) => <WorkbenchTabs .../>}</CollectorPanel>`;函数从不跨 server/client 边界。
- `data-workbench.tsx` 保持 server 组件:页面标题、结果横幅、静态区块(数据资产 metrics、原始批次、质量问题、兼容任务、逐笔与兼容)继续服务端渲染,以 ReactNode 槽位传入 `DataWorkbenchViews`。
- `page.tsx` 不再构造 `<CollectorPanel>` 元素,改传可序列化的 `collectorProps` 数据对象。

`CollectorPanel` API:

```ts
type CollectorSplitApi = {
  create: ReactNode;   // heading+chip、偏好/未连接提示、01 表单、02 预览侧栏
  results: ReactNode;  // heading+chip、错误横幅、任务选择器、03 任务详情、04 记录
  view: "create" | "results";
  navigate: (next: "create" | "results") => void;
};
children?: (api: CollectorSplitApi) => ReactNode;
```

- `children` 缺省 → 现有合并渲染(外层 section、工具栏切换器、双 hidden 视图),12 个既有用例零改动。
- `children` 提供 → 只返回 `children(api)`,拆分模式下 create/results 各自包在 `panel collector-workbench` section 中,保留 `.workbench` CSS 作用域与 `#independent-collector` 锚点。
- 两个半视图始终同时挂载(顶层 WorkbenchTabs 用 `hidden` 保挂载),表单草稿、任务轮询、退避计时不因切换视图丢失——这是既有「双视图 keep-mounted」不变量的延续。

## 2. URL 契约与导航事件

- 顶层视图键:`catalog` / `download` / `jobs` / `sources`(queryKey 仍为 `data_view`)。
- 拆分模式下采集面板内部 `view` 由 `data_view` 派生:`jobs`→results、`download`→create;缺省或旧值时回退 `collect_view`/`collector_job` 旧逻辑(深链接兼容)。`navigate`/`choose` 在拆分模式写 `data_view`,合并模式写 `collect_view`,两者都派发统一事件 `workbench-navigation`。
- `WorkbenchTabs` 订阅 `popstate`/`hashchange`/`workbench-navigation`,`select()` pushState 后也派发该事件——顶层 tab 与采集面板内部视图双向同步(在结果里选任务 → 自动跳视图 03;切 tab → 内部 view 跟随,错误双通道不错位)。
- `WorkbenchTabs` 新增 `aliases?: Record<string, string | ((url: URL) => string)>`:旧 `data_view` 值映射到新键;`collect` 是条件别名(`collect_view=results` 或带 `collector_job` → `jobs`,否则 `download`)。
- `page.tsx` 服务端做同一归一化决定 `initialView`(含 `collector_job` → jobs、universe_* → catalog、quality/composition → jobs、download/result → download+research_view=ticks、旧 schedules → sources+monitor_view=schedules)。
- 锚点映射:`independent-collector`/`financial-collection`/`distribution-collection`/`legacy-downloads`/`pool-download`(→catalog 的 `pool-download` 保留)按新视图更新;`collection`/`batches`/`quality`/`research-data`/`composition` → jobs;新增 `universe-catalog`/`universe-members` → catalog。嵌套 tab 的 queryKey(`research_view`/`asset_view`/`monitor_view`)与 anchors 原样保留。
- `data-actions.ts` finish() 改发新值:assets→jobs(带 asset_view)、ticks→download(带 `research_view=ticks`);`data-action-navigation.test.ts` 同步断言。
- SSO returnTo 白名单键集合不变(data_view/collect_view/research_view/asset_view/monitor_view/compose_*),旧参数继续透传后由别名消化。

## 3. 四视图内容映射(全部来自既有 panel,无新能力)

| 视图 | 主内容 | 嵌套(保留原 queryKey) |
|---|---|---|
| 01 数据目录 `catalog` | universePanel | — |
| 02 多维下载 `download` | collector.create | `research_view`:财务报表 / 分红送转配股 / 逐笔与兼容(原 ticks 分区内容) |
| 03 任务与结果 `jobs` | collector.results | `asset_view`:研究数据集 / 原始批次 / 质量问题 / 兼容采集任务(原 assets 分区内容,含 metrics) |
| 04 来源与新鲜度 `sources` | — | `monitor_view`:数据新鲜度 / 用量与配额 / 来源能力 / 自动更新(原 schedules 分区并入为第四个嵌套 tab) |

嵌套 `research_view` 增加 `ticks` 键复用原 queryKey,保证 `?data_view=fundamentals&research_view=events` 一类旧链接语义不变,新链接 `?data_view=download&research_view=ticks` 直达逐笔入口。

## 4. 组件拆分(B-P0)

- `collector-labels.ts`:states/errors/periods/PERIOD_ORDER/budgetWaits/observedDate/preferredSource(纯数据与纯函数,无 "use client")。
- `collector-create-form.tsx`:01 步表单 JSX 原样迁移;kind/period/source 的联动逻辑留在 `CollectorPanel`,以回调(onKindChange/onPeriodChange/onSourceChange/onSwitchPro/onSnapshotToggle…)下传,呈现组件无状态。
- `collector-plan-preview.tsx`:02 步侧栏(计划摘要空态、预览卡、最近任务)。
- `collector-job-detail.tsx`:任务选择器、03 步任务详情(分段/控制/分页)、04 步已发布记录。
- 合并模式 DOM 输出与拆分前逐字节一致(同一批 JSX 常量复用),`.collector-run`/`.collector-form`/aria-label 等测试选择器不变。

## 5. 渲染性能边界

`CollectorPanel` 每次轮询 setState 都会重新调用 children 函数;槽位 props(universePanel/assets 静态块等)引用稳定,React 对同引用元素 bailout,不会重渲染 RSC 槽位子树。嵌套 WorkbenchTabs 的 sync/scroll effect 因 `panels` 数组新引用而重跑,但操作幂等(setSelected 同值不触发渲染)。

## 6. 验证口径

- 12 个既有单测不改全绿;新增:拆分模式草稿跨视图保持、choose() 写 data_view=jobs、WorkbenchTabs 别名映射(旧值+条件别名)、data-actions 新视图值。
- 真实栈 Playwright:旧/新深链接矩阵、#锚点、SSO 登录后 returnTo、桌面+移动 390px 无横向溢出、只读角色视图强制 results。
