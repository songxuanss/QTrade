# 普通历史行情来源零值标志修复

真实归档诊断发现：601288.SH年度243条记录的原始sf均为精确归档文本`"0.0"`，旧v2转换仅识别数字0和文本`"0"`，把这些正常来源标志误判为suspect，并导致日线新鲜度证据拒绝。这是内部转换问题，不是已证明供应商全部停牌。

## 版本与语义

2026-10-18核对[MaiRui官方沪深文档](https://www.mairui.club/hsdata)：普通历史sf为停牌标志，零表示正常。Pro1分钟与完整年片的同名字段须分端点解释，本次不把普通历史政策扩展到Pro。

新增并注册`mairui.history-bars.v3`，仅用于`mairui.history.bars`：

- 有限数值按Decimal值比较，`0`、`0.0`、`0.000`、`0E-6`等零值正确识别，原始字段不改写。
- 普通股票的非零或未知标志继续可疑；空/缺失计入`source_flag_unknown_count`，不默认为零、不填造停牌或缺失行情。布尔、非法和非有限值在完整输入已留存后拒绝转换。
- 价格、成交量/金额精度与单位、时间标签及区间裁剪保持；新证据明确普通历史来源标志语义。旧history v1/v2及Pro v1继续原输出，旧质量/发布/冻结引用不覆盖。
- 映射版本属于覆盖键，新版本不会借旧版本的覆盖掩盖待处理范围。旧输入可明确预览v2→v3，生成独立新发布；没有检查点的旧任务不会因此被宣称可离线重处理。

代码注释说明精确归档词法与数值语义的区别、保留冻结版本的原因和Pro端点边界；普通history与Pro政策不混写到通用领域核心。

## 测试

`tests/test_bar_source_flags.py`最初17项中9项失败，复现多种零值误判、未知被当零和非法值未拒绝；修复后全部通过。再加注册范围、Pro拒绝与指数不适用政策测试，21项定向通过。指数普通历史按[官方指数字段表](https://www.mairui.club/hszsdata)不要求股票sf；缺失或额外同名字段不解释为股票停牌，元数据明确not_applicable。指数两例为受控回归，不是新的指数实接证明。证明旧v2的`"0.0"`输出仍可重现，失败时完整输入保留，价量未变化。

`scripts/selftest-normalization-inputs.py`最终分组130+14+20+20＝184项通过，含全部21项标志定向用例。真实临时PG/受控HTTP/队列/调度测试不称供应商实接证明。两生产模块mypy、Ruff、测试脚本ESLint/Prettier、OpenSpec严格校验及diff检查通过；v1 Schema没有重导出。

## 实际部署与真实UI

仅重建collector，镜像`sha256:30f867300eed2285b2348cec1348febe5a61fafdfe7ab121a109d49208922ca6`；Web及API/Worker沿用已恢复服务、原身份/数据库/归档。运行容器两份MaiRui模块的实际字节SHA与最终工作区文件逐项一致。没有新迁移。

### 原年度归档修复

```bash
cd apps/web
QTRADE_REPLAY_REPORT=bar-flags-replay node tests/verification-collection-replay-ui.mjs --bars-only
```

真实SSO页面明确选择原v2输入，预览v2→v3后确认；真实API/PG/Worker重新转换和质量检查，新发布243条，全部valid，质量报告passed且无source_suspect。原任务、发布、原始/检查点文件SHA不变，三页记录及市场新dataset刷新可见。前后scope信用/实际调用计数不增加，供应商调用0；该路径不是新来源下载。

管理员和采集READ两个身份分别在1440/390视口完成模态、质量报告、记录、市场链接和刷新，axe0/无横向溢出/无浏览器脚本错误。原权限/匿名/CSRF/跨scope拒绝继续通过；READ不能预览或提交。

`QTRADE_REPLAY_REPORT=bar-flags-replay .venv/bin/python scripts/verification-collection-replay-archive.py after`绑定精确新任务：RAW与旧发布完全一致；标准除quality纠正为valid外逐字段一致；新质量按原规则版本重放通过，无伪造子获取回执。22项迁移账本与文件SHA一致。

日线证据现在能验证新版本的历史完整周期，但获取时刻仍来自2026-10-17原history回执，新发布时间不当成采集时间。数据截至2025年末，**不代表2026年当前行情已补齐或当前新鲜度健康**；旧v2仍不可认证，不改旧判断或补造PIT。

### 新来源下载

```bash
cd apps/web
QTRADE_COLLECTION_REPORT=bar-flags-download node tests/verification-collection-compact-ui.mjs
```

实际页面新建601288.SH、2025-09-01至09-05、日线、all任务，经真实Worker/MaiRui/非空HTTP/history回执→RAW/标准各5条→v3/全部valid→质量passed→发布→市场消费者visible。双身份桌面手机浏览、刷新、质量/权限读取、明确新dataset的5根K线及图表刷新通过，axe0且无溢出；另保留243条原年度记录100/100/43分页检查。

`QTRADE_COLLECTION_REPORT=bar-flags-download .venv/bin/python scripts/verification-collection-compact-archive.py`核验精确任务/分段/发布、私有来源行集合/正文摘要、RAW和标准各5条、v3、全部valid、未知标志数0、转换输入与实际质量版本。不是仅健康检查或排队。`scripts/verification-bar-source-flags-http.py`另从已校验HTTP封套解码真实正文，确认原243条及新5条的sf均为JSON数值零，并将Decimal无损编码后的行摘要与RAW逐项绑定；这证明误判发生于内部词法处理。精确身份保存在私有review报告，密码/证书不进入截图、日志或Git。

## 六主业务与未完成范围

登录/权限、本次普通日线新下载和归档修复使用真实部署验证。策略版本、回测/报告及连接交易控制未改/未重测，沿用主矩阵限制；告警未新增真实触发，本次仅验证历史发布证据与原获取时间，不称完整健康告警链交付。Pro逐端点语义、全部4.x规则/原始解码、日频调度/新鲜度完整矩阵和一致恢复等继续目标，整体18/42保持。
