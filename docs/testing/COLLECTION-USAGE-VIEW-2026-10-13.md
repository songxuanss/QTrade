# 当前空间用量视图验收

OpenSpec `complete-professional-data-workbench` 保持13/42。用量快照及拒绝留存是5.x的部分实现；队列等待、存储用量、公平领取、计划确认/硬规模、定时/复核及全部来源路径仍未验收。

## 契约与实现

- 独立GET `/v1/usage`、QTrade GET `/data/collector/usage`都要求当前作用域READ权限；浏览器不传其他scope/source。采集模块不读取QTrade业务表，QTrade通过公开接口读取，不直接查采集库。
- [UsageSnapshot JSON Schema](../../packages/data-contracts/schemas/v1/UsageSnapshot.schema.json)与严格模型携带当前空间、来源日时区、取得/重置时间、两层预算及有效预留、当日调用/结果/字节、拒绝次数及最近详情。模型拒绝未知版本、非信用账单语义、负数/强制转换、矛盾结果或日时区、摘要明细超合计及重复身份。
- 共享来源只返回预算及预留汇总；请求/拒绝详情全部按当前source+scope+day查询，不返回其他空间主体或明细。详情各最近50条，汇总不截断；预留数还包括前日未到期占用。REPEATABLE READ READ ONLY保持同一快照，查询不扣账、不写到期状态。
- 追加0009拒绝表：出站未执行且不扣信用；拒绝事务回滚后单独持久化安全原因，避免诊断随事务回滚消失。相同拒绝request_id只保留首次事件，真实UsageGate每次尝试产生新身份。没有故意耗尽用户真实预算来制造拒绝。
- UI用独立服务器action读取/刷新并保留输入，显示当前空间及共享来源预算、重置、结果与最近明细。默认折叠明细，手机预算纵排，表格局部滚动，失败保留旧快照并明确不是当前额度。来源证书、服务token、租约token、URL及上游私有响应不进入公开契约。

## 检查与证据

- `scripts/verification-research-persistence.py`：42项隔离真实PG测试通过，新增scope/denial/expiry/只读及55条汇总不被50详情截断，以及真实PG+HTTP当前READ鉴权、跨scope/匿名拒绝。上游故障仍使用受控传输，不算真实源故障。
- `pytest tests/test_usage_contract.py tests/test_collector_http.py -q --tb=short`：17通过，验证严格新契约、身份与当前角色拒绝；独立模型从JSON往返兼容。
- Mypy四个Python模块、Ruff、Web TypeScript/ESLint/Prettier及生产构建通过，导出新Schema没有改写已有v1 Schema。
- 初次部署实际UI测试发现heading-order：卡片主标题h3跳级。修复为h2及预算子标题h3，重建部署后6项实际SSO/API/持久用量、键盘刷新保持输入、真实request_id明细、桌面手机axe0/无横向溢出、刷新/跨scope/匿名401、只读成员手机读取通过。
- 随后补充浏览器/BFF传输失败的异常捕获，防止按钮一直busy或将失败读数改成零。最终7项实际UI检查通过，新增浏览器断网→刷新明确错误/按钮恢复→旧快照时刻保留→恢复网络再次刷新成功；此路径为受控浏览器故障，不声称实测供应商故障或服务断电。
- 5项Web读取/日期回归通过。采集主链曾在Web重建并行期间登录跳到auth/error，明确记为失败，原因未证实；部署稳定后重新执行三条主链脚本通过：8项行情/因子（384/5/96）及六财务版本、31事件桌面/手机刷新与axe检查，不用旧结果掩盖此前失败。
- 本轮最新4个非空行情/因子发布通过公开分页、raw/standard摘要、原始捕获及质量、市场消费者数量复核（`verification-independent-collection.py`启用raw-captures/quality开关），不只依据UI完成字样。
- collector已应用0009及旧0008；当前collector镜像 `sha256:ade0d4a892e76d7649a0a486becc0f3b431f02f4e6dcaaf6e2e134da899927b4`。最终Web故障捕获镜像 `sha256:1c17a63e86d80738eee5ebbec86dfb2fa418101971b6e3f40a58f4d4f8f96090`。实际入口13000、认证API3122、独立collector18100，保留原库与归档。

当前截图在本机私有review目录，包含实际安全用量和request_id，没有凭证。手机视图已人工检查，预算和文字层级清晰，宽表在局部区域滚动。禁止将用量快照、预留或目录存在解释成当前供应商权限、物理socket仍活跃或专业研究就绪。
