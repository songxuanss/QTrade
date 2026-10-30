# 工作区新鲜度健康总览

本次接通当前作用域已有核验记录或后台配置的只读总览，不是所有数据的健康覆盖证明。日线旧归档仍不能证明来源最新状态；最新来源观测、其他类别更新策略和真实供应商异常到恢复的完整验收尚未完成，8.1–8.3继续未整体勾选，整体17/42。

## 设计与 review 重点

- 独立契约 FreshnessHealthQuery/Entry/Page，严格身份、作用域、状态/年龄与时钟、唯一排序和游标约束。scope+完整口径摘要定位，来源、类别、周期、调整和映射版本不混合。新增三个JSON Schema，无数据库迁移。
- 仓储单页 REPEATABLE READ READ ONLY，统一数据库read_at，10秒statement timeout；snapshot/watch集合取limit+1判断后页，每页最多100项。跨页不承诺冻结快照，新增或变化后从第一页刷新。名称来自同作用域真实发布，缺少时明确未核验。
- 核心每页当前READ，验证仓储输出和本地注册目录，不调用供应商。尚未检查、检查过期、较新后台失败、未来时钟、来源未注册/未配置均不能借旧current显示健康。配置存在不证明授权已验证。旧检查状态作为历史证据保留。
- collector服务身份+当前READ结构化POST；QTrade会话GET仅接收limit/after_key，推导actor/scope并重验返回边界，no-store。页面读取不创建检查、watch、下载，也不领取或确认事件。最近事件的消费端确认不等于最终渠道送达，提供告警中心入口。
- UI默认折叠、按需读取，20项/页，名称与代码同显；桌面卡片网格、手机单列、局部证据展开。浏览器单调耗时使旧检查过期；读取失败保留旧证据但撤销当前健康样式，按钮释放可重试。工作区切换重建面板状态。关键领域/时间/权限/并发边界已补注释。

## 隔离测试

`.venv/bin/python scripts/selftest-freshness.py`：214项通过。包括保存current超龄/未来时钟、未注册/未配置、无检查watch、较新失败、撤权先于仓储、重复/错scope/游标倒退拒绝；真实隔离PG游标遍历、HTTP身份及严格参数拒绝；读取不调用供应商、不claim/ack；真实租约/确认状态只读可见；原核验/outbox/投递/后台与架构回归。

首轮213通过1失败：新用例的未消费事件污染共享临时库的后续全局claim断言。健康PG用例现各自使用独立自有随机数据库并finally删除，保留原队列断言；修复后全部通过。未在部署数据库删除测试事件或重置数据。

Ruff、6个修改/新增模块Mypy、Web TypeScript/ESLint、JSON Schema导出及OpenSpec严格验证通过；实际Web生产构建成功。

## 实际部署与 UI

13000 Web→3122当前会话API→18100独立collector→原独立PG。保留持久卷和身份配置，不重建数据。当前镜像：

- Web：`sha256:c1e1edbc6260035667788023afcda787553e9d7fc7d2b34a469cf21b534d4862`
- collector：`sha256:703f76a076bf1d346dc2b53bda1d5ca96b83b02cf4ea337385b7fb4cdef80eb3`

`cd apps/web && node tests/verification-freshness-health-ui.mjs`：实际SSO管理员1440桌面及采集READ risk_manager 390手机通过。该角色有正常告警处置权限，不称全站只读。

- 专用非实盘验证空间使用原真实20股票年度发布，配置为停用watch；另有之前明确标识的受控投递fixture。共21个口径，第一页20/第二页1，无重复遗漏，返回与公开API一致，上一页恢复。重复运行不修改已有watch或启停。
- 简称农业银行与代码可见；未检查显示尚未核验；受控fixture来源未注册，不能借历史恢复current显示为健康。之前真实年度归档检查仍保留实际状态，不产生新的供应商成功/失败结论。
- 真实读取前后snapshot、watch及consumer_state相同，刷新/重开仍一致。没有新增行情下载或来源探测。
- 浏览器受控中止显式刷新请求：旧卡片保留且每项待核验、错误可理解、按钮释放；解除故障后真实重读恢复。此为传输故障，不是供应商真实停机证据。
- 键盘展开、分页、匿名401、跨空间拒绝、非法limit/cursor422、axe0和无页面横向溢出通过。截图与私有结果在`.tools/verification-local/review/freshness-health-*`，不包含凭证。

`node tests/verification-freshness-ui.mjs`：原单发布管理员核验/采集只读手机、超龄、读取失败、无记录、真实归档重新核验与刷新回归通过。总览默认折叠不改变原工作流。

配置及接口见[独立采集运维说明](../operations/independent-collector.md)。仍需完成最新来源独立观测、因子/研究类别规则、真实来源故障恢复及目标其他分组；本报告不宣称整条Goal完成。
