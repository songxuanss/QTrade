# 研究转换异常与安全恢复

本轮补齐OpenSpec 4.1/4.4相关错误处理，不勾选完整任务，整体仍17/42。原始归档、规则全集、有界离线重处理、新处理版本与旧冻结引用完整验收仍需继续；这里不是完整重处理功能。

## 问题与实现

ResearchCollectionExecutor原来只对显式DataFailure(unverified)保存版本化转换阻断。Pydantic类型校验等ValueError会跳过该记录，ResearchWorker的未知异常也没有及时完成租约，只能等待外层循环和租约过期恢复。

现在转换边界将ValueError归为unverified，在已持久原始页之后保存相同严格版本的安全阻断；不保存异常正文、不填造标准数量。其他DataFailure保持原安全码。Worker对未预期异常以unavailable完成当前租约，UI可明确失败并手工重试；未知故障不能冒称已证实的输入拒绝，所以不生成转换阻断记录。最终写入仍受仓储当前租约防护，写库失败不伪装已落库。没有增加自动重试或退款，也不修改冻结计划/原始manifest/发布schema。

关键注释说明类型校验边界、供应商拒绝与未知故障的区别、凭证安全及恢复保留旧证据的原因。

## 验证与部署

- 自建唯一research_test_数据库，完整迁移后运行研究collection/jobs/budget wait/evidence/processing contract：45项通过，无跳过。测试后只删除该自建隔离数据库，不操作业务数据。
- 显式unverified与ValueError两种受控转换拒绝均先存原始页、保存未知标准量的阻断；重复首次检查保持，修复转换后同冻结输入取得2条标准记录，旧原始批次和失败记录保留。
- 受控RuntimeError经真实租约Worker进入failed/unavailable，无publication、原始可读、无虚构输入拒绝记录；手工重试恢复2条发布，原始manifest逐字节不变。异常字符串不进入任务错误码。
- collector已重新构建部署，镜像 `sha256:6c4cbfd2caa23ac79c4167d9f12dc0ee938df3f67f05b35a75bdfade24eb40be`，Web和认证API沿用现有版本。健康确认后执行测试。
- 部署受控财务/事件fixture使用ValueError边界，executor捕获真实raw并保存阻断，随后SQL插入terminal failed运行供UI读取；没有queued生产Worker可领取，不调用供应商、不伪造信用、不发布fixture市场数据。不计UI创建/供应商失败证明。
- 真实SSO→13000 UI→3122 API→18100→真实DB/归档的两类拒绝UI通过：精确运行/批次/检查身份、无发布/标准量未知、键盘、桌面操作与390px无溢出/axe0、刷新旧检查保持、匿名401和跨空间拒绝。手机截图已观察；工作台长期积累任务的完整页面仍很长，不能据此宣称总体专业布局验收完成。
- 正常真实来源UI回归：新建财务income任务得到6个披露版本，公司行为任务31条事件；两者桌面/手机axe、浏览/刷新通过。按精确UI-created request/publication再验证部署卷，非空raw/standard、内容版本及摘要一致。成功主链与受控失败证据分开记录。

## 命令及界限

隔离PG执行 tests/test_research_collection_postgres.py、test_research_jobs_postgres.py、test_research_budget_wait_postgres.py、test_research_evidence.py、test_research_processing_contract.py。Ruff和两模块Mypy通过。

部署后执行 `scripts/verification-research-processing-fixture.py`，在apps/web执行 `node tests/verification-research-processing-ui.mjs`、`node tests/verification-financial-ui.mjs`、`node tests/verification-distribution-ui.mjs`，随后 `scripts/verification-research-ui-archive.py`。均退出0。测试报告与截图仅保存私有review目录，不提交凭证、原始供应商URL或异常字符串。

这些结果不证明供应商真实失败后的恢复、执行中重启、异常分页回执完整保留、独立有界离线重处理或全部研究就绪。原目标和全部未完成项保留。
