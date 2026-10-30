# 研究数据解码前响应留存与回执UI

本阶段补齐财务/事件HTTP状态拒绝、业务错误或JSON解码失败时缺少响应原始证据的问题。**HTTP响应不等于有效原始页、标准数据或发布成功。** 完整4.1/4.4仍未完成，整体18/42；部分传输字节、异常分页、全部类别规则、有界离线重处理及旧引用完整验收继续保留。

## 实现及 review 要点

- 独立v2 ResearchResponseReceipt/Page与Schema，不修改原研究计划、RAW页、标准产物或发布模型。安全回执绑定scope/request/actor/plan/source/kind/mapping/标的/分片/游标，含获取身份/响应索引、HTTP状态、正文完整标志、字节数、SHA及记录时间；无正文、URL、头、异常文本或虚构标准数量。
- 财务三表与有限事件实现同步fetch_with_capture，使用原获取/预算管道。ResearchAcquisitionEvidence管理一次fetch的独立窗口和身份，最多10份响应；关闭后回调拒绝、未捕获响应拒绝、吞掉的存储错误也不能放行。注释说明捕获时点、私有正文和解析页的区别、窗口关闭与不可变引用边界。非HTTP受控适配器可沿用原RAW接口，不据此声称有HTTP证据。
- 完整收到的解压后HTTP字节先私有不可变写入、fsync，再追加0020索引，之后才判断状态及解码JSON。记录时间不是PIT可知时间，每次实际fetch新身份不借旧正文时间。正文对象repr隐藏，权限目录700/文件600，无浏览器正文端点。
- 0020只新增research_response_receipt及索引/不可变触发器，不修改已锁定迁移。载荷摘要、计划/分片绑定、scope/request外键，拒绝UPDATE/DELETE/TRUNCATE。5秒连接、10秒语句、5秒锁等待；索引失败阻断后续，已写私有正文保留，不假称回执已公开接受。
- 当前READ及精确任务/计划查询；公开collector GET `/v2/research/jobs/{id}/response-receipts`和会话桥接。limit1–100、按captured_at/receipt_id最新优先、同任务游标、严格Scope/数量复核和no-store。元数据读取不重新访问供应商或重验私有对象当前完整性；内部response_body才校验实际字节/SHA。
- 任务行入口按需打开独立只读对话框，刷新/分页、错误及旧记录参考、暂无记录的未知语义；HTTP 200非校验/发布成功。原始表格内长列表被滚动区裁切，改为原生模态对话框后视觉复核。桌面/手机、焦点/键盘/Escape、axe0和无页面溢出通过。

## 隔离验证

`.venv/bin/python scripts/selftest-research-acquisition.py`：54项通过，六组分别使用独立自有research_test_*数据库，最终仅删除这些临时库。包含新回执/契约/桥接、旧财务/事件、研究采集/队列/预算等待/原始证据及转换阻断回归。首次扩大测试52通过2失败，原因是不同队列套件共享临时库、Worker全局领取了别组遗留任务；改为每组独立库，保留原断言后全部通过，不清理生产数据或放宽领取行为。

覆盖401、HTTP 200非JSON、200业务拒绝、转换拒绝均先保存正文/元数据；无产物/发布。索引写失败停在解析前，私有字节仍在；同正文多次fetch、重开读取、相同回执幂等、首次时间保持、同时间分页无重复遗漏、错误/跨任务游标、scope/内容冲突、数据库不可变及正文同长度篡改拒绝通过。保留回调、缺回执、吞掉写入失败、未知公开字段/URL/正文/伪造数量/布尔状态拒绝也通过。

Ruff、10个新增/修改生产模块Mypy、Web TypeScript/ESLint/Prettier、生产构建及OpenSpec严格校验通过。部署collector八个相关生产模块与工作树逐字节SHA一致。新Schema仅新增两份，未改写冻结发布Schema。

## 真实供应商主链与持久证明

原13000→3122会话API→18100独立collector→原PG/归档/Worker，原身份和持久卷保留。collector镜像`sha256:f5f3ad0bd3f507424a6a0c36b72758d61c087001324114c23b1152b3afe81cc5`，最终Web `sha256:4928ef64ad3fd9eddc8d2c0e0ce868de12e06d40e70f3871c5b5cee2428e0c81`。0020实际账本校验和与部署包文件一致。

- `QTRADE_RESEARCH_SCOPE=<专用验证作用域> node tests/verification-financial-ui.mjs`：实际SSO/UI创建新income 2015报告期任务，Worker消费、MaiRui响应、非空RAW/标准、质量/发布、6披露版本浏览及刷新通过。
- 同作用域 `node tests/verification-distribution-ui.mjs`：实际UI创建事件任务，Worker/来源/归档/门禁/发布、31事件版本及刷新通过。两者桌面/手机axe0和无溢出，精确request/publication/content_version匹配，不借旧行。
- 首轮财务测试观察默认API会话作用域，未明确使用UI所选验证作用域，源任务实际已completed但观察器超时。增加安全阶段诊断、明确selectedScope及精确任务行/发布版本后通过。没有把超时或旧行计成功；已产生的真实完成产物保留，不删除重建数据。
- `scripts/verification-research-response-archive.py`：按这两次UI创建的精确任务/发布读取部署对象，验证HTTP 200完整回执、非空正文长度和SHA，解码/Decimal原值处理后与已归档RAW页逐项相同，覆盖该发布全部RAW批次，标准记录/发布版本及摘要保持。只输出身份/数量/校验结果，不输出正文或证书。

真实获取时间只是取到旧报告/事件的时间，不证明历史PIT或来源全历史；标准数据仍保留原单位未知等研究限制。

## 受控失败UI及权限

`scripts/verification-research-response-fixture.py`使用MockTransport及独立限定executor，直接生成已终态failed专用夹具，不向生产Worker投queued任务、不调用供应商或伪造信用。金融401重复21次用于真实回执存储/分页，事件200非JSON一次；**不是21次真实供应商错误，不是UI创建或Worker重试证明**。未产生原始解析页、标准数据或发布，私有正文实际可验。

`node tests/verification-research-response-ui.mjs`：真实管理员1440及采集READ risk_manager 390分别读取两项新真实下载和两项受控失败。21条按20+1前后翻页、精确ID、记录SHA显示、刷新重开、失败/已发布行状态、受控中止读取后旧参考/按钮释放/恢复通过；匿名401、跨空间拒绝、非法limit/cursor422、正文路径404、axe0/无页面溢出通过。risk_manager有其正常告警权限，不称全站只读。

对话框版再次完整验证并查看实际截图。读取不改变任务/发布，响应状态与质量状态分别说明；私有结果在review目录，未提交凭证或原始供应商URL。

配置/接口及边界见[研究证据说明](../data/RESEARCH-EVIDENCE-V2.md)。本阶段不是离线重处理，未取得完整响应的部分字节和异常分页留存、全部发布规则及目标其余分组仍需继续。
