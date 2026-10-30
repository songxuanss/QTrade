# 研究 RAW 重处理公开闭环

本次补齐522b0c2研究核心的collector HTTP/生产Worker、QTrade会话桥接、财务/事件模态操作及真实归档重处理验收。**整项4.4和整个Goal仍未完成，整体18/42保持**：行情/因子重处理、仅HTTP字节的解码修复、全部类别门禁/异常分页矩阵继续待完成。

## 边界与review

- 独立POST `/v2/research/replays/preview`、POST `/v2/research/replays`、GET `/v2/research/replays/{request_id}`使用严格v2命令、服务鉴权、当前权限及no-store。预览无任务副作用；重复request/control返回原任务，过时control409。
- 生产普通及离线队列共用PostgresResearchReplays，ResearchWorker明确接入同一服务。0021追加不可变意图/父RAW输入血缘；父计划、RAW、失败和发布不改写，离线任务缺接线不回退网络。配置可收紧页/行/字节上限，不能通过浏览器增大。
- QTrade的collector_replay模块只通过公开契约访问，当前会话派生主体/空间；管理预览/提交要求DATA_MANAGE及CSRF，读意图READ。返回严格核对作用域/请求/摘要。前端只显示有界投影，无正文、文件路径、Cookie或证书。
- 共用模态组件让财务/事件表格保持紧凑；显式预览分配一次新身份，模糊提交失败沿用相同身份和control重试。关闭窗口不取消工作。供应商0调用不等于本地计算/存储无成本，标准数量/发布由Worker及原门禁确定。采集时间与未知PIT保持，不把重新处理时刻当历史可知时间。
- 关键注释覆盖身份与血缘分离、原子入队、离线不回网、处理上限及模糊提交恢复。公开行为与配置见[独立采集运维](../operations/independent-collector.md#研究-raw-离线重处理)。

## 隔离回归

`.venv/bin/python scripts/selftest-research-acquisition.py`最终82项通过：31+11+11+7+5+12+5，各队列组使用自己随机临时PG库并只清理自有库。新增HTTP实际经过QTrade桥接→collector路由→PG→Worker→标准/质量/发布，受控转换器的fetch/partitions/estimate设为抛断言，不计新供应商接入。覆盖严格版本/额外字段、身份注入/READ拒绝、预览无任务、控制冲突、幂等、刷新读取、当前撤权、安全配置错误及来源不出站；核心七项仍覆盖修复转换/损坏/缺页/解压上限/旧引用不变。

新增研究查看者回归与原桥接合计12项（包含上述31）：READ查看者与任务创建者不同，来源请求身份为当前查看者，主体注入拒绝，跨空间/版本/游标/摘要响应拒绝；保留原query冒用身份403测试。FastAPI TestClient现有httpx弃用warning保留，不以warning作为集成失败。

## 部署 UI 与实际结果

`node tests/verification-research-replay-ui.mjs`在13000 Web、3122原API、18100独立collector、真实数据库/对象与Worker执行：

- 原600519.SH收入表2015报告期6披露版本、原公司行为31条均来自前次真实MaiRui响应的精确冻结任务。此次从这些已归档RAW创建两个新请求/新发布，**不是重新下载供应商数据**。
- 实际SSO管理员通过键盘打开模态，受控中断预览请求后显示恢复提示、按钮可重试；恢复预览不创建任务。明确确认后Worker完成，新任务/新发布身份不同，重复提交只返回该新任务，近期列表仅增加一项。
- 当前工作区实际usage请求数及信用前后相同；父公开清单完全一致，标准非空6/31且原门禁passed/warning。浏览新财务/事件版本并刷新仍显示精确内容版本/数量。
- 原采集READ risk_manager实际登录、读取两个新任务的父血缘、浏览/刷新版本；无管理按钮，带有效CSRF预览/提交仍403。缺CSRF、匿名、跨空间、主体注入拒绝分别核验。
- 桌面1440和手机390的实际模态axe0、页面无横向溢出、浏览器脚本错误0；截图人工复核。模态有界内部滚动，不扩展工作台页高。
- `scripts/verification-research-replay-archive.py`绑定精确新任务：原/新RAW内容和原采集时刻一致，原始/标准实际Parquet摘要有效，父冻结版本不变，未知knowledge/known_at不补造；新任务HTTP回执0；生产全部迁移账本与源文件摘要一致，含0021。

### 发现的真实缺陷及修复

首轮管理员已完成两个新发布，但READ财务浏览失败；前端原query把任务创建者actor放入请求，API正确拒绝与当前viewer不一致的身份。新增 `/research/records`只读会话投影，前端不提交actor/scope，桥接派生当前READ用户并重验严格ResearchRecordPage身份/版本/游标/摘要/数量；旧 `/research/query`保持原冒用身份拒绝，未放宽权限或冒用管理员。修复后重新运行完整实际UI，管理员及READ两类结果/刷新通过。首轮两份已发布产物保留，没有删除失败证据或重写原任务。

## 普通下载与部署版本

生产统一仓储接线后，还分别从UI新建普通财务和事件下载：`QTRADE_RESEARCH_SCOPE=<专用验证空间> node tests/verification-financial-ui.mjs`及distribution对应命令。两次真实MaiRui响应、Worker、非空RAW/标准、门禁、发布、6版本/31事件消费和刷新均通过，桌面手机axe0；`verification-research-response-archive.py`绑定这两次精确新请求的HTTP字节/摘要、RAW/标准/发布及迁移账本通过。其实际出站与离线0调用验证分时顺序运行，不能把普通下载算作离线成本。

最终Web镜像`sha256:f4ef2735cd007f92dad2a1c0212f23835520d68a7eef6c01701c82d2ed6c799b`，collector镜像`sha256:fdf8ab4dbfee9373a27ef9e3927e519d0bcb13cee74f8b655088d51bb6ef538a`；保留3122原API身份/持久化及数据库/归档卷，仅重启自有API并重建Web/collector。生产构建含TypeScript、目标ESLint/Prettier、9文件Ruff格式、五模块Mypy及OpenSpec严格校验通过，暂存变更不含凭证或私有验证目录。最后服务器显示投影显式选择字段，不随共享请求增加字段而自动转发身份信息；日期轴提供中文标签。

## 重启与验收限制

实际重启独立collector、保留数据库/卷/配置后，执行 `node tests/verification-research-replay-ui.mjs --read-only`重验同一新请求/意图、原/新发布、消费者及刷新；该命令不重新预览或提交。管理员/READ、财务/事件四组读取、消费者/刷新及桌面手机模态复验全部通过，当前空间来源请求/信用仍未增加。此为完成后持久化重验，不是执行中强杀、全局灾备或跨机器恢复。

源转换修复→阻断恢复由隔离PG受控转换器验证；此次live路径使用原已注册转换，不伪造供应商缺陷或声称改变了来源公式。没有新来源请求、未重新认证当前权益/历史覆盖，也未新增PIT能力。行情/因子、异常页及仅HTTP证据的重处理未完成，4.4不勾选。策略版本、回测/报告和交易控制未修改/未重新验收，仍按六主业务矩阵限制。
