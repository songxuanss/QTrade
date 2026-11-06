# Tasks

## 1. 拆分独立采集面板(行为不变)

- [x] 1.1 抽出 `collector-labels.ts`(states/errors/periods/PERIOD_ORDER/budgetWaits/observedDate/preferredSource),`collector-panel.tsx` 改为导入。
- [x] 1.2 抽出 `collector-create-form.tsx`(01 步表单)、`collector-plan-preview.tsx`(02 步侧栏)、`collector-job-detail.tsx`(任务选择器+03/04 步),JSX 原样迁移,联动逻辑留在 `CollectorPanel` 以回调下传;合并模式 DOM 不变,12 个既有用例零改动全绿。

## 2. children-as-function 组合模式

- [x] 2.1 `CollectorPanel` 增加可选 children(api: create/results/view/navigate);缺省保持现合并渲染;拆分模式 create/results 各自包 `panel collector-workbench` section,保留 `.workbench` CSS 作用域、`#independent-collector` 锚点与错误双通道语义(页脚内联 vs 结果横幅,唯一 alert)。
- [x] 2.2 导航事件统一为 `workbench-navigation`;拆分模式 `navigate`/`choose` 写 `data_view`(download/jobs),合并模式写 `collect_view`;`view` 派生兼容旧参数(collect_view/collector_job)。

## 3. 顶层四视图装配

- [x] 3.1 `workbench-tabs.tsx`:新增 `aliases`(string 或 (url)=>string);订阅并在 select() 后派发 `workbench-navigation`。
- [x] 3.2 新增客户端 `data-workbench-views.tsx`:实例化 CollectorPanel(render props)+顶层 WorkbenchTabs 四视图(catalog/download/jobs/sources),槽位接收 universePanel、嵌套入口 tabs、资产 tabs、监控 tabs。
- [x] 3.3 `data-workbench.tsx`(server)重组:静态区块(assets metrics/原始批次/质量/兼容任务、逐笔与兼容)改为槽位;嵌套 tabs 保留 research_view(新增 ticks 键)/asset_view/monitor_view(新增 schedules 键);锚点映射按新视图更新并新增 universe-catalog/universe-members。
- [x] 3.4 `page.tsx`:collectorProps 数据下传;initialView 服务端归一化(旧 data_view 别名、collect_view/collector_job 条件、universe_*→catalog、quality/composition→jobs、download/result→download+research_view=ticks、schedules→sources+monitor_view=schedules);returnTo 白名单键不变。
- [x] 3.5 `data-actions.ts` finish() 改发新视图值(jobs/download+research_view=ticks);更新 `data-action-navigation.test.ts` 断言。

## 4. 测试与门禁

- [x] 4.1 新增单测:拆分模式草稿跨视图保持、choose() 切 data_view=jobs、WorkbenchTabs 别名(静态+条件)、锚点兼容;运行 web 全量 Vitest。
- [x] 4.3 CI auth specs 与验证脚本适配:login.spec.ts 数据段断言改四视图;verification-workbench-layout.mjs 重写为四视图正典验收(含 origin 环境覆写);9 个 openDataView 调用脚本机械迁移到新视图/嵌套 tab 名;verification-collection-compact-ui.mjs 适配拆分模式(`#collector-results` 钩子、顶层 tab 导航、reader 无创建表单断言)。data-workbench.spec.ts 本体不改(旧 URL 恰好验证别名链路),其 4 张截图快照在 production build 后重新生成。
- [x] 4.2 全量门禁:tsc、eslint、prettier、Vitest、openspec strict、constitution、architecture(后端未动则 pytest/ruff/mypy 快速确认不受影响)。

## 5. 真实栈验证与交付

- [x] 5.1 构建并重启本地真实栈,Playwright 深链接矩阵:旧 data_view 七值、collect_view/collector_job、#锚点、universe_* 参数、SSO returnTo;桌面+移动 390px 四视图无横向溢出;草稿跨视图保持;只读角色强制结果视图。截图存证。证据:`.tools/verification-local/review/b1-deeplink-matrix.json`(admin 41 项深链、嵌套默认 4、草稿跨视图、390px 无溢出、reader 默认结果视图+3 项显式参数)、`data-layout-results.json`(双角色 axe0/无溢出/草稿保持/监控实读)、`collection-compact-ui.json`(243 条分页、双宽度、双角色 frozen chart);截图 `b1-matrix-*.png`、`data-layout-*.png`、`collection-compact-ui-*.png`。三脚本独占轮次 exit 0(并发 Playwright 负载会致 30s/90s 超时 flake)。
- [x] 5.2 迁移债务披露:其余钉死 13000 旧端口的 collector 流脚本(independent-collector/plan-guards/default-range/preferences/budget-wait/factor-catalog/collection-replay/quality/runtime-recovery/normalization-input 等)在 B1 之前即已陈旧,不在本变更内全量重写;提交说明中如实披露。
- [x] 5.3 审查 staged diff(无凭据、不含 apps/api/security.py 旧改动),按里程碑提交并推送;如实披露未尽事项。

## 6. B2 UX 增强(范围条/继承分组/抽屉/移动已选栏)

- [x] 6.1 `collector-scope.ts`:手工标的解析/合并/移除与范围摘要纯函数,单测覆盖去重、继承计数与日期/来源标注。
- [x] 6.2 全局范围条:类别、标的(含"继承自数据目录"标记)、日期、来源四段;点击切视图并聚焦字段;不改变提交语义。
- [x] 6.3 继承态分组:冻结股票池分组 + 已继承池 chips + 解除继承;手工标的与日期不受影响。
- [x] 6.4 标的清单抽屉:搜索、股票池分组、批量加入/移出、dialog 语义、焦点循环、Escape 关闭与焦点归还。
- [x] 6.5 移动端 sticky 已选栏:390px 显示标的/因子数量与清单入口,不遮挡提交、无横向溢出。
- [x] 6.6 真栈验收:`verification-b2-scope-ux.mjs` 桌面+390 截图与 JSON 证据(`.tools/verification-local/review/b2-scope-ux.json`、`b2-scope-*.png`):范围条反映草稿、解除继承保留手工标的、抽屉加入/移出与焦点归还、Escape、范围条跳转聚焦、axe 0、390 无溢出;证据使用真实 MaiRui 股票池快照(北证50, 50 成员)。布局/深链矩阵/紧凑脚本回归全绿;Vitest 104 项、tsc/eslint/prettier/openspec strict 全绿。
