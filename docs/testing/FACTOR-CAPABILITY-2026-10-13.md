# 因子能力与范围校验阶段

OpenSpec3.2实施中，未勾选。移除三项北向因子的统一“待开放”禁用：目录存在允许选择，不认证账户历史权益。已有实测north_holding_ratio最新值非空、选定历史空，故不能由名称归类或空响应推断无权限。available只表示目录可选，采集仍会真实验证权限；单位、计算版本、频率无字段保持unknown。

历史单分片最多365个自然日，调用前拒绝超限；返回超过365条、重复日期或日期落在冻结时间范围之外拒绝，避免静默截断/接受越界。空历史是无观测，不伪装非空成功、不标权限拒绝。新增内容摘要绑定的factor-evidence文件随元数据缓存保存证据语义，目录响应不提升为history/live_e2e。

25项独立采集测试通过，包括北向可选/空响应、单位频率未知、超范围零调用及越界拒绝；ruff/mypy通过。当前证书有界实测目录85项，north_holding_ratio可选；600519/2025-09-01至09-06北京时间半开历史返回空，availability_evidence仍unknown，未断言历史权限。前一次目录取得后因测试请求漏schema_version本地校验终止，没有因此发出历史请求；修正后验证通过。

本轮使用Adapter真实请求样本，未部署新行为或执行UI/Worker验收；现有部署仍旧规则，下一步部署及真实因子主链验证。原始先存门禁改造、历史/最新能力细分、背景缓存/过期状态及默认年/增量继续按Goal完成；3.2尚不宣称全部交付。报告不包含证书，元数据私有缓存.tools/data-probe/factor-adapter-20261013。

## 部署与真实UI追加

collector镜像sha256:9ebf94c2aa3ea85b7c6d134cf71cbb25aa2b0ad8975b09451ef39f0e2d47731d已部署本轮规则。`cd apps/web && node tests/verification-factor-empty-ui.mjs`通过：13000真实登录，北向持股比例可选、600519/2025-09-01至09-05含结束日、UI预览→提交→真实Worker→实际空响应，最终no_data且无publication，整页刷新仍明确“无观测数据”。这不是权限拒绝，不能据此证明其他历史范围无数据或来源仅最新能力。报告factor-empty-ui-results.json私有无凭证。

随后`QTRADE_VERIFICATION_ORIGIN=http://127.0.0.1:13000 node tests/verification-independent-collector.mjs`8项真实UI检查通过，包括pe_ttm历史5点浏览/刷新、分钟384条、冻结池96条、重复/补缺计划、权限/跨作用域及桌面手机可访问性。`verification-independent-collection.py`对这批4个非空发布再次核验原始/标准归档和公开分页摘要/消费者导入通过。目录样本与UI一致，按3.2任务勾选。

本轮不完成因子默认自然年、分类搜索、后台缓存更新/过期状态、完整增量、修订与用量/新鲜度，更不完成v1原始先存/全门禁；这些对应其他未完成任务。旧段落“未部署”等为前阶段状态，以本次追加为准。整体Goal保持未完成。
