# 自动计划接口与工作台分组验收

本轮完成OpenSpec7.1，整体15/42。7.2–7.5、因子/研究更新策略、实际撤权/重启及异常恢复完整矩阵仍未完成；不声明全部自动更新交付。

## 行为与模块

collector独立schedule_routes仅用data-contracts/core，正式GET/POST `/v1/schedules`、GET单计划、POST启停。当前READ查看、manage创建/控制；header主体/作用域必须匹配草案。创建/启用前检查资产/市场、来源配置/策略、标的硬限及元数据授权；保存默认disabled，启用再次验证冻结主体，revision CAS拒绝过期控制。来源检查可能产生有账本的元数据请求，不创建下载。分页limit1–100、offset有界，稳定排序，仓储只当前scope。

QTrade独立collector_schedules桥接从会话派生actor/scope，DATA_MANAGE+CSRF才可变更；明确标的优先，否则冻结一个snapshot与content hash。空范围、无效标的、非严格bool/整数和外部actor/scope字段拒绝。共享CollectionScheduleControl只包含revision，两应用不相互import运行代码；source仍作为契约字段，当前仅已验证日线策略开放。导出27份v1schema，仅新增control，旧schema/产物不改。

工作台自动更新panel与手工编辑分开：折叠创建、标的/股票池、北京时间/日周季、回看/补跑/批准、明确自动ACK、保存停用→核对→启用；状态、暂停原因和分页。旧读取失败保留旧列表并提示，异步代次/unmount避免旧读取覆盖新操作。Cookie/CSRF仅server actions，表单无凭证。停用不取消已入队任务、不删除产物；冻结输入修改应新建计划。最终桌面计划卡片适配多列，手机单列并检查展开编辑器；明确MaiRui来源。

## 验证

- 调度相关39项单元/契约测试通过；`scripts/verification-research-persistence.py` 98项隔离PG回归，包含HTTP→真实PG的创建/重复、启停/重开、read拒写、scope拒绝、CAS、严格bool与分页。fixture受控政策不计live。新增配置catalog夹具首轮遗漏cache参数，修正夹具后完整通过，没有降低校验。
- TypeScript、ESLint零警告、Prettier、Ruff、Mypy七模块及生产build通过。初次React effect同步setState/依赖lint失败后改为外部读取回调、代次和unmount保护；未忽略规则。Web构建首轮registry TLS超时，在terminal后重试成功，不并行启动相同构建。
- `verification-schedule-controls-ui.mjs` 真实13000 UI：键盘创建、未ACK不能保存、disabled持久/刷新、启用、实际部署timer/Worker产物、停用/刷新；CSRF403、过期revision409、不可访问workspace404通过。API故意对不可访问资源404，未泄露存在性。管理员与READ账号共享同一空间，后者有列表/刷新而无写控件，带自身有效CSRF的enable也403。
- 桌面1440/手机390在部署panel axe0，无页面横向溢出。`verification-schedule-layout-ui.mjs` 只读展开编辑器+最终卡片布局再次验证axe0/无溢出，截图复核；不额外创建任务。最终CSS仅改善该panel的内边距、卡片布局和状态标签，业务逻辑不变。

## 真实与复用证据及异常

首轮UI贵州茅台：job `6f60e681-cd5a-5e68-aaed-1ede7063f2f4` 自动生成5条非空发布并完成consumer，创建/启停/刷新已通过；测试最后因browser.newPage的默认context不兼容axe而失败。改成显式newContext，不把工具失败当成功。

第二股票招商银行：job `5f8ba9f1-fdcd-564b-9876-12bfc0bf7815` 在合法窗口返回no_data，解码history receipt response_rows=0，HTTP回执留存且publication不存在。240秒非空结果断言失败，finally已停用计划；不计live成功，不将原因猜成停牌/无权限/供应商不提供，该样本的可用性仍未建立。保留零观测原始证据，后续来源核对及新鲜度应覆盖此限制。

修正工具后的贵州茅台UI：plan `73fc2825-c3a0-4730-b9c1-b1e12c9d90c9`，job `5bc9bece-91f6-53ff-8aef-ad5fdfe78938`，3组UI检查通过，5条/消费者visible。补缺复用首轮同空间已完成不可变发布，未声称此轮新增HTTP历史调用；与首轮真实来源非空证据分别记录。确认后通过UI停用、finally再次幂等停用；4条验证计划均disabled，数据不删。

带新UIjob运行RAW_CAPTURES/QUALITY的 `verification-independent-collection.py` 复核5个非空发布的raw/standard/源回执/门禁/分页/consumer数量通过。部署后手工主UI8项回归通过（384行情、5因子、两成员池96，桌面手机刷新），不由此推导各股票/类别全部可用。

最终collector `sha256:5797afcddb79324405d46c75fd453b9257558fa8631cbcf9db15b062a9f8fc0e`，Web `sha256:662b999a750c0f3dee1f5baccb25013c88af4804b70634696e8d7a061c6d77b8` 均healthy；认证API3122保留原配置重启且health200，入口13000。没有新增迁移或改既有迁移/卷。OpenSpec严格validate与提交凭证检查必须通过；全Goal持续推进。

后续同原招商银行job已通过真实UI重试恢复5条，原0行证据保留；本报告描述初次失败时状态，不代表该job仍失败。详见COLLECTION-EMPTY-RECOVERY-2026-10-14.md，诊断与观察模式、实际动作及成功分别记录。
