# 来源能力的逐项证据目录

本次为独立采集的七项已注册MaiRui能力补充历史、单位、调整、时间、更新、权限、配额、许可八项说明，并列出三个尚未接通的扩展要求。当前成员与其他已登记来源尚需统一呈现及验收，3.5继续未完成，整体17/42；本报告不把十项说明当全源覆盖。

## 设计与审查

- 新增严格SourceProfileCatalog/SourceProfile/CapabilityFact与三个独立v1 JSON Schema。每项事实有证据等级、核对日期、仓库文档引用；未知必须有明确确认条件。八项不能重复或缺少，catalog来源唯一、最多100项。
- 证据说明与冻结ResearchCapability/ResearchPlan分开。不修改旧模型、发布、控制摘要或原Schema；编辑说明不能改写旧研究引用。注释解释这一边界、配置与权限的区别及只读行为。
- MaiRui本地装配普通行情、Pro分钟、因子、balance/income/cashflow、有限分红送转。配置状态来自本地适配器，读取不访问供应商，不宣称当前账号全部权益成立。历史证据仅限报告场景，股票单位不外推指数，分钟09:30边界限制保留；普通财务不改称PIT。
- 历史有效成员、退市身份、复权因子为not_connected，映射为空，无采集按钮或虚构可用端点。未接通不等于供应商不提供。平台预算不冒充供应商套餐配额；用途许可未知，没有据此新增分发/付费资源。
- collector GET `/v1/source-profiles`服务身份+当前READ；QTrade GET `/data/collector/source-profiles`由会话推导actor/scope，严格验共享返回和scope，no-store。原采集/研究能力发现接口保持。
- 工作台默认折叠，按需读取、选择查看八项事实；故障保留旧说明并待重新核对，重试恢复。证据引用在产品界面用可理解的报告名称呈现，准确路径保留在契约供审查。桌面两列、手机单列，选择框主题一致、44px操作高度与键盘聚焦样式。

## 测试与问题修复

`.venv/bin/python -m pytest tests/test_source_profiles.py tests/test_freshness_bridge.py tests/test_architecture.py tests/test_research_contracts.py tests/test_research_plan_limits.py -q`：79项通过。包括完整八项、真实文档路径、重复/缺项/未知缺确认条件/URL引用拒绝；配置缺失、服务身份、当前撤权、无供应商调用；会话scope/actor、跨scope坏返回拒绝；旧能力模型序列化保持及原计划/研究契约与架构回归。

Ruff/Mypy四个新增或修改模块、Web TypeScript/ESLint/Prettier、生产构建和OpenSpec严格校验通过。Schema导出仅增加三个文件，没有改写既有Schema。

实际部署脚本：`cd apps/web && node tests/verification-source-profiles-ui.mjs`。真实SSO、13000 Web→3122会话API→18100collector，使用原工作区/身份与持久数据库。

最终UI镜像`sha256:524aba07180f551407ebd29e6ec1344db1630d330f4587feb321db97895395f9`；collector镜像`sha256:e55b8e5851230810e049b21ced159d7da54c6fa221dcd19efdced69c744af1eb`。引用路径穿越拒绝加固已包含在最终采集镜像，并保留原配置重启会话API；未修改既有持久数据。

- 管理员1440桌面及采集READ risk_manager 390手机：十项说明、每项八事实/核对日期/未知确认条件逐项可见；七项已注册来源与原公开catalog/research catalog的身份、类别、周期、映射、市场、资产和配置状态一致。
- 键盘展开、选择、刷新/重开、匿名401、跨工作区拒绝、axe0/无页面横向溢出通过；未接通项只有说明，没有预览/下载控件。risk_manager仍拥有其正常告警权限，不称全站只读。
- 受控中止显式刷新：旧说明仅供参考、按钮释放；解除故障后真实重读恢复。此为传输故障，不是供应商真实故障、授权成功或新下载证据。读取前后同一目录内容一致。
- 首次UI验收遇原API未退出导致重启脚本中止，登录回调失败，未计通过。按记录的进程身份终止自有API进程组，保留命令/私有配置后启动，实际ready及登录恢复；未重建账号、密钥或数据库。测试错误输出改为阶段/错误类型，避免原始浏览器错误包含回调参数，临时失败日志已脱敏。
- 第二次实际UI找到标签包裹全部option导致精确无障碍定位失败，改为useId关联独立label/select并重新部署后通过。截图发现选择框为原生灰条，进一步统一主题/操作高度；最终截图复核与结果在私有review目录。

新目录读取不发起供应商请求，未创建下载任务、执行数据迁移或清理生产数据；原页面其他元数据刷新与既有后台行为不归入这一只读操作的证明。曾验证的供应商样本与真实下载证据沿用公开报告并分别标等级，不把只读UI通过当供应商新实接。剩余当前成员/其他来源统一目录、完整质量重处理、自动规则和Goal全矩阵继续推进。
