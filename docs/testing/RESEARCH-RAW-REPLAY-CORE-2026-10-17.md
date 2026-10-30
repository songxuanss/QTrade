# 有界研究RAW重处理基础（尚未部署入口）

此阶段实现离线研究RAW重处理核心与持久化基础，**不代表4.4完成，也没有对用户开放HTTP/UI重处理操作**。用户本轮页面布局要求优先交付；本基础作为独立提交保留，后续继续装配公开入口及真实UI验收。整体18/42不变。

## 已实现及review要点

- 新增独立v2 ResearchReplayRequest/Replay/Limits/Submission及四份Schema，不改写旧计划、原始、标准或发布Schema。新任务身份和父请求分开，固定原计划/所有RAW清单、目标转换版本、输入上限和控制摘要；供应商调用估计明确为0，不推断标准记录数量。
- ResearchReplayService只调用当前注册转换器的capabilities/normalize，不调用fetch、partitions、estimate或来源规划；没有网络证书但已注册的本地转换器也能处理归档。权限在预览、提交、每页执行及最终归档前重新核验；发布沿用当前授权及原版本化质量门禁。
- ordered_inputs按冻结分片及游标链排序，而非按UUID排序。缺分片、缺页、循环、重复游标或未连接页拒绝，不能暗中出站补页。默认最多1000页、100000原始行、64MiB输入；执行同时遵守冻结上限和当前更严格的限制。
- 私有replay_raw在解码前限制文件读取字节及Parquet元数据的未压缩字节总量，逐页扣除两者较大值；校验实际SHA、行数和内容摘要。压缩后很小的重复字符串仍受未压缩上限约束，不只按行数保护。
- 新请求复制不可变RAW并保留原collected_at，重新转换和发布不伪造历史可知时间。共用normalize_page处理转换拒绝，保留新处理的安全失败记录和父任务原失败；原发布、引用、RAW及标准对象不覆盖。
- PostgresResearchReplays将新run、0021不可变意图/输入血缘及既有租约job在同一事务保存；失败回滚整个新意图。父RAW外键、列载荷绑定、控制摘要及读取重验；5秒连接/锁、10秒语句上限。既有register抽出同连接内部方法以复用事务，不改变原调用接口。
- ResearchWorker新增可选接入。显式research-replay-plan.v1缺少重处理装配/元数据时失败，不回退供应商采集。冻结转换能力与当前注册不匹配时拒绝，不能静默使用另一个版本。
- 关键注释说明只读预览、源输入与新任务的界限、处理时间与采集时间、原子提交、连接清理及无网络回退。该模块仍属于独立data契约/核心/Adapter，不引入QTrade业务或供应商SDK依赖。

## 隔离证据

`.venv/bin/python scripts/selftest-research-acquisition.py`：66项通过，七组使用各自随机临时PostgreSQL库，19+11+7+7+5+12+5；仅删除本次自有临时库。七项新测试覆盖：

- 完整父RAW→当前v2转换→新发布及旧发布引用/私有字节不变，重复提交幂等、首次预览无新任务；将fetch/partitions/estimate改为必抛断言，证明该路径没有调用它们。
- 完整RAW转换阻断→修复转换→离线新发布，父失败报告及raw_persisted状态保持；未知可知时间及原collected_at保持。
- 缺页、页/行/字节硬上限、压缩前大载荷拒绝，原始输入不可被静默缩小。
- 接受后正文损坏导致Worker失败/无发布；执行前撤权、目标转换变化和当前上限收紧均拒绝；未装配的Worker不回退网络。
- 无效RAW引用在暂存新run后触发冲突，事务回滚run/意图/job；SQL不可变约束拒绝改写。

首次新增测试将configured=false与缺少unblock_condition拼成无效fixture能力，被既有契约正确拒绝；补充真实禁用原因后通过，不放宽产品验证。Ruff及七个修改生产模块Mypy通过。Schema导出使用`PYTHONPATH=packages/data-contracts/src .venv/bin/python scripts/export-research-replay.py`；首次未设源码路径不能导入，之后按项目源码路径生成，未修改冻结Schema。

## 未交付边界

本核心提交522b0c2时，0021仅在自有临时库执行，生产collector尚未迁移或装配ResearchReplayService；没有HTTP/会话桥接、用户预览/提交/UI、生产Worker重处理或真实来源历史重放的交付证据。异常分页及仅HTTP字节的解码修复、行情/因子重处理、整项4.4及旧引用的全部类别验收仍待继续。受控转换器与本地PG通过不证明新供应商或PIT/历史研究能力。

后续公开装配与真实归档重处理证据见[公开闭环](RESEARCH-RAW-REPLAY-DELIVERY-2026-10-17.md)，应结合该报告判断当前部署，不能将本次提交时点的边界解释为现状。
