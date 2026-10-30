# 来源目录闭环：当前成员与独立分组故障

本报告完成固定范围的OpenSpec 3.5目录验收。当前运行注册表为MaiRui：普通行情/Pro分钟/因子、普通财务三表、有限事件及当前成员共八项实装能力；历史有效成员、退市身份与复权因子三项未接通要求明确限制，共11项说明。每项包含历史、单位、调整、时间、更新、权限、配额、许可八方面，未知记录条件而不补造。目录可读不代表当前来源权限、配额或用途许可已确认，不代表这些未知研究能力已可用。

## 边界与 review 要点

- 独立SourceProfileDirectory只接收DataAuthorizer与严格、不可变的说明元组。每次读取复核当前READ并重新验证嵌套契约；没有成员解析、网络、仓储或交易业务依赖。说明修订不修改UniverseContent/成员冻结摘要；已有研究计划和发布模型保持原样。
- 发现类别current_membership与历史membership_interval分开，不混入行情/因子DatasetKind。mairui.reference使用实际分类版本mairui.hszg.v1，明确证券简称/代码/交易所/关联，没有价格、数量或权重；观察时刻不是历史生效起止，缓存重读不重标观察时间。
- 当前参考服务由API组合根装配，其说明必须使用本部署的MAIRUI配置，不借独立collector配置声称可用。没有参考凭证时仍能只读unconfigured说明，不触发解析或供应商请求。
- 新公开会话GET `/data/universes/source-profiles`与collector桥接返回同一版本化契约。工作台通过公开API并行组合，要求scope相同且来源唯一；不导入供应商或数据库。未来参考模块独立部署时，可保持契约替换地址。
- 单组失败显示缺失分组并保留另一组实际说明，不能将部分目录当全部；两组失败保留旧说明且待核对。当前成员头部明确“当前快照，不支持历史日期查询”，未接通项显示“查询规则待接入”。只读选择不扩大下载范围或创建任务。
- RQData/Tushare等未注册本轮实际采集流程，固定范围不要求新增采购或账户。新增来源必须同步逐项证据契约；已有库/文档不计已实接，也不把项目未接入推断为供应商不提供。

## 验证

Python：`pytest tests/test_reference_source_profiles.py tests/test_source_profiles.py tests/test_data_reference.py tests/test_freshness_bridge.py tests/test_architecture.py tests/test_research_contracts.py tests/test_research_plan_limits.py -q`，91项通过。覆盖配置/未配置、当前READ/撤权/跨scope、嵌套坏值拒绝、不调用reference供应商、当前/历史语义、原参考快照及研究计划/契约回归。

Vitest：`npx vitest run src/features/data/source-profile-actions.test.ts`，4项通过；同scope组合、单侧及反向故障、两侧失败、重复身份与跨scope拒绝。均是明确受控测试，不计供应商实接。Ruff/Mypy五个核心/组合/适配模块、TypeScript/ESLint/Prettier及生产构建通过。

实际部署：13000 Web→3122认证API，参考元数据由独立核心提供；下载侧18100collector保留原镜像/持久卷。Web镜像`sha256:914225e8bd1a42c85342f7f37dd4be0c2e55d6d58260acd953aa23e84bdeb37e`，collector `sha256:e55b8e5851230810e049b21ced159d7da54c6fa221dcd19efdced69c744af1eb`；API按记录的自有进程身份保留配置重启，没有重建身份、数据库或原归档。

`cd apps/web && QTRADE_VERIFY_PROFILE_OUTAGE=true node tests/verification-source-profiles-ui.mjs`：

- 真实SSO管理员1440桌面、采集READ risk_manager 390手机：11项/每项8事实与独立接口一致，七项采集来源与旧catalog/research catalog的市场、资产、周期、类别、映射及配置一致；当前成员来源、类别及当前快照限制可见。
- 三个not_connected扩展直接提交认证且有效CSRF的预览，均400/unsupported，前后研究任务列表一致；没有伪造可用入口或创建queued任务。
- 中止浏览器刷新为受控传输故障，旧说明仅供参考、按钮可重试；实际HTTP重读恢复。新组合法不以fixture响应替代部署接口。
- 实际停止原collector容器，已登录页面刷新来源说明后仅当前成员1项可读，同时明确缺失“行情与研究采集说明”；参考公开接口仍200。恢复原容器后11项完整、提示消失。finally确保失败也恢复原容器；数据库/归档未修改，后台及API恢复ready。不扩大解释为整个模块停服冷启动或全部供应商故障矩阵。
- 刷新重开、键盘、匿名401、跨工作区拒绝、axe0、无页面横向溢出通过。截图和私有结果在review目录；role有正常告警权限，不称全站只读。
- 首次完整目录等待断言未通过，安全日志仅记录阶段/错误类型，当次状态不足以确定根因，未计通过。补充只读面板安全诊断后，两轮真实全量/停服恢复验证均通过，未再复现；不以重试结果捏造首轮根因。

新说明读取无供应商调用由接口装配及受控禁止网络测试证明；整个工作台原有元数据后台不属于这一操作的无调用承诺。本轮没有新行情下载/新供应商成功或失败证据；已验证的数据样本及闭环引用沿用有日期、范围的审计报告。

## 完成范围

3.5只验收本轮来源目录、证据分级、未知/未接通限制、公开边界与运维扩展说明。整体18/42；质量重处理、完整自动规则、用量、恢复与Goal全矩阵继续未完成。使用与扩展见[独立采集运维手册](../operations/independent-collector.md)。
