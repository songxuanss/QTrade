# 计划估计、规模硬限与确认阈值

整体 OpenSpec 保持13/42，5.2仍未完成；本阶段先接行情/因子，研究类别和确认后真实阈值执行尚待验收。

## 行为与边界

新增严格 `CollectionCostEstimate`、`CollectionPlanGuard` 及独立 schema；可变预览新增可选字段，旧不可变 `CollectionPublication.schema.json` 与 HEAD 逐字节相同。规模硬限不截断日期、标的或因子；已完成/在执行分段不计新增数。覆盖摘要绑定当前策略，提交重验，确认只接受JSON布尔，原请求身份重复提交幂等。四项配置和估计范围见运维手册。

调用估计只含后续新增分段、按当前来源机制给保守区间；缓存可减少至零，不含预览元数据、重试或后续复核，不代表供应商账单。预览不创建任务，但元数据缓存不足可触发真实且记账的请求。确认不能越过当前授权、规模硬限或出站信用网关。

## 已运行的验证

- `.venv/bin/python scripts/verification-research-persistence.py`：61项隔离真实PG通过。新增用例证明标的硬限在名称调用前拒绝、分段超限不创建任务、未确认不创建、策略变化旧摘要拒绝、新预览明确确认可创建、重复返回同任务、确认不能绕过硬限。来源为fixture，不计供应商真实执行证据。
- `pytest tests/test_collection_plan_guards.py tests/test_collection_planning.py tests/test_collector_http.py -o addopts='' -q --tb=short`：16通过，覆盖两服务布尔严格性、区间倒置/负值/虚假计价拒绝、规模配置、纯估计、来源时间分段及HTTP鉴权。初轮测试未准备模块导入所需环境、构造了非法因子请求、错误删除冻结身份，失败记录后修正测试；未放松产品校验。
- Ruff、Mypy、前端TypeScript/Prettier/ESLint及生产构建通过；OpenSpec严格校验通过。关键策略/接口补充review注释。
- `node tests/verification-plan-guards-ui.mjs`：13000实际SSO/UI→3122认证API→18100独立服务/真实持久化，5项通过。601288.SH普通5m的2023–2025全量范围只预览，不启动大下载：确认框默认未选/提交禁用、键盘确认启用、390px无横向溢出/axe0、日期修改使预览与确认失效、新预览重新未选。实际API未确认400、数字/字符串422，无任务创建，刷新工作台仍可访问。
- 首次UI脚本以browser.newPage创建上下文导致axe工具报错；改为显式newContext重跑通过。首次未取得axe结果，不计通过；没有通过强制点击或绕过鉴权修正。
- `QTRADE_VERIFICATION_ORIGIN=http://127.0.0.1:13000 node tests/verification-independent-collector.mjs`：8项实际主链回归通过，单股5m384条、所选历史因子5点、两成员股票池96条；包含重复/补缺、拒绝、权限和桌面手机刷新。随后开启 `QTRADE_VERIFY_RAW_CAPTURES=true` 与 `QTRADE_VERIFY_COLLECTION_QUALITY=true` 运行公开持久化校验，4个非空发布的原始/标准摘要、来源capture、质量记录及市场消费者一致。行情可复用真实来源年度缓存，因子历史仍向已授权来源请求；不把缓存读取写成新增行情RPC。

私有脱敏证据 `.tools/verification-local/review/plan-guards-ui-results.json` 与手机截图；未保存登录截图、请求头、证书或密码。

## 部署与未完成项

验证拓扑不变：13000 Web→3122当前认证API→18100独立采集，保留原数据库/对象卷；API保留原配置重启。collector镜像 `sha256:82a360c503eb74e68b8b5c1f1370252f5c489e63ea69aba5272eeba45468b28b`，Web镜像 `sha256:13aa5c24fcb8b1ff95dce09cb046ee534e23661ae9fc12ecb8c5053112045db2`。

真实阈值确认后的小范围来源执行、研究预检估计/阈值、按来源与空间的计划尺度、全部配额/权限矩阵仍待完成；大范围仅预览的成功不能作为历史来源覆盖证明。自动调度、新鲜度告警、完整质量重处理、性能/隔离恢复及其余目标保持未完成。
