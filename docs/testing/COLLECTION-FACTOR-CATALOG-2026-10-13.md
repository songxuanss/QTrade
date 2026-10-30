# 持久因子目录与后台更新阶段

## 行为与接口

供应商目录网络请求从页面读取路径移到独立后台任务。新增 v1 `FactorCatalogSnapshot`，经 `/v1/factor-catalog` 和认证业务桥接查询，包含严格版本、字段、摘要、取得/尝试时间、fresh/stale/loading/unavailable/unconfigured、后台更新和安全失败原因。目录存在不是当前账号历史授权或覆盖证明，契约固定 `not_established_by_catalog`。

`FactorCatalogCache` 作为独立 Adapter 内模块负责校验、持久化和状态。0600 临时文件/fsync/原子替换，读状态锁不覆盖网络或磁盘提交。最多1000项、文件4MB；重复ID、坏摘要、错误版本不采用。旧带摘要的目录有界导入为 stale，未知取得时间保留未知。损坏当前快照不静默降级旧文件。后台每60秒检查，有效期15分钟；失败保留旧字段且结束更新中状态。因子预览/执行仍调用有效缓存或实时更新，实际历史请求继续检查响应与授权。

UI 提供缓存/时间/失败说明、“更新目录状态”及目录摘要，状态刷新不强制重发供应商请求。普通来源/任务和质量报告读取不再等待供应商目录。

## 受控测试

`.venv/bin/python -m pytest tests/test_factor_catalog_cache.py tests/test_mairui_cache_concurrency.py tests/test_independent_collection.py tests/test_collector_http.py -q --tb=short`：45项通过。

新增覆盖保存/重启、过期和失败保留、未知时间旧目录迁入、损坏拒绝、bool/string/未知版本拒绝、慢请求期间即时快照、过期执行重新校验权限、1001项拒绝结束刷新、原子替换失败保留旧文件与内存。独立接口无服务身份401、跨作用域403、READ可读取未配置状态。供应商响应和权限故障为fixture，不计真实来源成功。

mypy修改的5个Python文件、ruff、Web typecheck、3项Vitest响应/目录回归、相关ESLint/Prettier及生产构建通过。

## 部署与实际UI

实际13000 Web→3122认证业务API→18100独立服务；保留业务数据库、原始归档和对象卷，重启API使用原配置。部署镜像：

- collector `sha256:6c3d06ccd6059585acef897fcddd4a57cca21c90c617f4b5e407684f0725dda6`
- Web `sha256:b4a5eff5cc0258a70d78cad5827d253f93e8dba4d3fae8f9781b7c89ffb54636`

`cd apps/web && node tests/verification-factor-catalog-ui.mjs`：6项通过。实际目录85项，来自之前真实取得的持久不可变目录；state=stale、fetched_at=null、error_code=timeout，摘要 `fd05fec610d7602275dd2782bb440bab5555b5c95dca140b6a087687c8a6b0c5`。后台更新失败时接口仍200，三次阶段读取分别56/54/75ms；不是性能目标或全市场吞吐结论。collector重建/重启后同数量和摘要恢复。

UI显示缓存及权限/时间限制；键盘刷新等待实际RSC响应完整结束，标的/日期输入仍保持；页面重载后可浏览相同目录；1440/390无页面溢出且采集区域axe0；匿名业务接口401。私有证据 `factor-catalog-ui-results.json`。

`node tests/verification-collection-quality-ui.mjs`：既有四类质量状态/UI/API/刷新、跨任务404、桌面/手机/键盘7项回归通过，私有证据 `collection-quality-ui-results.json`。所有报告无凭证。

## 未完成与外部失败

实时目录当前timeout，未新建因子采集，不能声明实时目录恢复、当前历史权限或新非空下载通过。实际新格式快照写入已用fixture验证；此阶段的真实重启证据来自旧的有摘要目录，不冒充一次新的供应商响应。

跨实例刷新协调、统一预算、其他元数据目录持久化、全部来源故障/角色/撤权场景和新下载主链仍待验收。6.5保持未勾选、整体12/42；本阶段不会降低固定真实接入范围。运维操作见 `docs/operations/independent-collector.md`，设计与Review注释同步OpenSpec。
