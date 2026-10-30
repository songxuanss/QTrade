# 研究类别公开接口与新增来源示例

本文件最初记录发现/计划/分页查询契约阶段。现已有普通历史财务三表及有限分红送转真实接入、持久发布和查询；其限制与证据见接入范围和阶段报告，未知来源仍拒绝，不提供fixture可用入口。v1行情/因子及旧冻结研究计划契约保持原有路径。2026-10-14新增执行控制详见[运维说明](../operations/independent-collector.md#计划规模调用估计及确认)：独立`/v2/research/plan-preview`返回ResearchPlanPreview，旧`/v2/research/preview`继续返回ResearchPlan，Provider另提供无副作用的estimate端口，提交控制不进入冻结产物。

## HTTP

沿用采集服务身份 Bearer 及 `X-Data-Actor`/`X-Data-Scope`，每次回查当前权限；浏览权限不能预览创建意向，预览要求manage。请求主体/作用域必须与请求头相同，不通过调用者提供的body扩大权限。

| 操作 | 路径 | 契约及拒绝 |
| --- | --- | --- |
| 来源发现 | GET /v2/research/catalog | ResearchCapability；启用/证据等级、类别、市场、身份、日期轴、时区、历史边界、映射/可知限制及解阻条件 |
| 无副作用预览 | POST /v2/research/preview | ResearchPlanRequest → ResearchPlan；不创建任务、不获取供应商数据，明确日期轴/半开范围/标的/模式与冻结计划摘要 |
| 分页查询 | POST /v2/research/query | ResearchQuery → ResearchRecordPage；明确产物/内容版本、类别、opaque cursor/limit；as_of只有历史可知证据足够才可启用 |

未知schema版本或额外字段422；身份/跨作用域403；未知来源或不支持的类别/日期轴/历史范围400；未配置来源503；无产物404；冻结版本不匹配409。没有悄悄换源或截断范围。错误仅安全code，不包含上游URL/凭证。

日期轴分 report_period/observation_date/effective_date/event_date。声明可用轴不是宣称任意查询都具备PIT，as_of另有证据和查询校验。供应商计划分片须连续准确覆盖每个明确标的的请求区间，不能漏段/重叠/加入额外标的。计划摘要绑定完整请求、能力和分片/规划版本；调用估计给区间与依据，未知分页上界不伪装精确预算。

分页记录具有作用域、产物、冻结内容版本、类别、当前/后续cursor及页摘要。查询层再次核对这些字段、数量、来源，拒绝跨源、错误页和未来/未知可知时间记录；下一cursor不能等于当前cursor。ReadPort必须只返回质量通过且已发布版本，后续持久化实现必须证明这个约束。

## 新增来源/类别的可运行受控示例

[tests/test_research_interface.py](../../tests/test_research_interface.py) 的 ControlledSource 展示最小Adapter：

1. `capabilities()`：声明金融类别、报告日期轴、历史起点、市场与映射版本。证据标fixture，实际应用不能改成live_e2e。
2. `partitions(request)`：准确覆盖冻结标的及半开日期区间，不访问数据库或来源。
3. `fetch(request, partition, cursor)`：返回 ResearchRawPage，第一页有 next_cursor、第二页结束；任意供应商字段只留在原始证据，不包装为标准行情。
4. `normalize(request, page, collected_at)`：返回明确 FinancialStatement 类型，未知单位/可知时间不补造。

ControlledReader 演示独立产物引用与分页，返回两个不同披露版本；查询要求明确内容版本，受控有证据/未知时间路径分别验证。该Reader不持久化、不执行质量门禁，因此不是完整新增类别生命周期验收。

```sh
.venv/bin/pytest tests/test_research_interface.py tests/test_research_contracts.py
```

测试使用真实collector路由和权限代码、HTTP TestClient及受控Adapter，区别于实际部署/真实供应商验收。验证目录、纯预览、无权限/跨域/未配置、范围边界、缺段重叠、Provider游标、公开分页/摘要、冻结版本以及无证据as-of拒绝。后续2.4～2.6必须补持久化/原始归档/门禁/发布及完整受控生命周期；3.3～3.4须在现有授权下完成真实接入，不由本示例替代。

## 研究任务接口

POST `/v2/research/jobs`接受同preview的严格v2请求，以request_id幂等冻结计划并入队；需要manage授权。GET `/v2/research/jobs/{request_id}`需read授权，返回state、attempts、publication_id和安全error_code。queued不表示成功；completed只有真实Worker门禁发布后形成，no_observations没有发布。接口读取当前作用域，正文actor/scope不一致403。任务当前整计划执行，暂未提供取消、失败重试或缺失覆盖API，不能据mode字段推断这些行为已经实现。

来源目录现接通MaiRui普通财务balance/income/cashflow，保持sample证据及未知单位/PIT限制。原先空目录状态是接入前记录，其他研究类别仍未接通。实际income2015 API/Worker证据见MAIRUI-FINANCIAL-ADAPTER-2026-10-13测试记录；尚无UI完整验收。

## 工作区浏览器桥接（待部署UI验收）

业务API `/api/v1/workspaces/{workspace_id}/data/collector/research`增加catalog、preview、jobs、jobs/{request_id}、publications/{publication_id}及query。preview接受类别、来源、标的、日期轴/范围和模式，actor/scope/correlation由服务端登录上下文生成；提交与查询使用preview形成的严格v2身份并验证一致性。读操作需READ，预览/提交需DATA_MANAGE与既有mutation/CSRF检查，服务token由CollectorClient在服务端添加，不返回浏览器。

`tests/test_research_bridge.py`使用受控权限依赖及远程客户端验证只读写拒绝、服务端身份、跨作用域/未知schema拒绝、明确版本查询和路径传递；加既有collector_http共4项通过。此证据是fixture，不能代替真实身份/部署/UI验证。接口已实现但本阶段尚未重启业务API或增加页面，OpenSpec6.4仍未完成。

## 页面持久任务读取准备

增加GET /v2/research/jobs和工作区桥接GET /research/jobs：返回当前作用域最近100条任务（内部上限200），包含冻结request、执行状态、尝试次数、发布ID和安全错误码，按创建时间排序。不以浏览器localStorage保存任务充当持久化。新增隔离PG测试验证重建Repository后列表正确、跨作用域空列表和上限拒绝；研究持久验证合计30项通过。

Web增加research-collection-types、服务端actions和workspaceResearchCollection加载器，沿用workspaceRequest的登录/CSRF及no-store路径。预览、提交、列表、清单和明确版本分页查询留在服务端，浏览器不接触采集service token。Web typecheck及新增文件eslint通过；Python四文件mypy通过。本轮尚未连接页面组件或部署新增列表接口，不计UI验收，OpenSpec6.4继续未完成。
