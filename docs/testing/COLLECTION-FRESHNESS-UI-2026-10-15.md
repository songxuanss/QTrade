# 新鲜度检查记录的部署UI

本阶段接通当前已有真实日线快照的QTrade桥接与结果面板，完成8.3的只读展示分组。自动探测/持续刷新、来源与其他类别完整规则、告警和真实上游故障矩阵仍未完成；8.1–8.3保持未勾选，整体17/42。

## 行为与边界

采集完成的日线分段提供“查看新鲜度快照”，按需读取，页面首次加载不为每个分段探测供应商。桥接GET `/data/collector/publications/{id}/freshness`以当前READ会话先取scoped不可变manifest，再从source/标的/日线/调整/映射推导key；浏览器不提交actor/scope或任意key。严格解析返回清单与快照，错身份/非法结构拒绝，成功no-store。QTrade只依赖公开契约和collector HTTP，不导入collector应用/读取其表。

返回该逻辑数据集的最近检查，可能汇总其他发布；不是仅对所点旧批次重新判断，也不触发来源检查。snapshot原checked_at与判定保留，桥接附服务器读取时间/年龄/阈值及未来检查时钟警告。

`QTRADE_FRESHNESS_SNAPSHOT_MAX_AGE_SECONDS`配置在QTrade API运行环境，默认900，范围1–86400，重启API生效；已在.env.example和运维说明记录。它控制检查记录是否过旧，与来源发布grace_seconds是两回事。UI按服务器年龄加浏览器performance elapsed持续老化，不依赖浏览器墙钟。过期、读取失败或时钟异常显示待核验，不继续把旧current当当前健康；保留上次判定/证据作参考，失败恢复后可重新读取。

面板展示检查有效时长、来源更新宽限、预期截至、来源/本地/发布周期、采集/发布时间、检查时间及预计可获取时间（时间明确北京时间），缺证据显示未验证/规则未知。显示上次检查的原因与下一步，未知和异常用警示色，手机单列/桌面响应网格。未接入类别不提供假造快照入口；现阶段入口只在日线结果中出现。

没有快照404明确显示“暂无检查”，不把下载完成当新鲜度核验。刷新按钮只是读已有记录，不触发新检查；页面清楚说明这个能力边界。暂未提供用户点击生成检查或生产持续刷新，不能将此面板宣称完整新鲜度监控交付。

## 验证

18项桥接/既有研究/collector HTTP测试通过：READ会话及manifest推导、真实key的服务主体透传、no-store/检查过期、错scope/publication ID在查询前拒绝、不支持周期/调整拒绝、错快照/源错误拒绝、时钟异常标记；受控测试不算部署证明。Ruff/Mypy、Web TypeScript/ESLint/Prettier与生产构建通过。

最终Web镜像 `sha256:263e07b4f1272a0ef9e077bed4b28708c88d416cf720b6388f49e45a6665072a`，API3122保留原配置和进程身份安全重启，健康200后才执行UI；collector保持421e2622…已部署版。

`verification-freshness-ui.mjs`实际SSO管理员及共享空间只读角色→13000 UI→3122 session API→18100→真实PG快照通过：

- 直接打开已完成年度任务，从精确601288发布定位面板；真实snapshot为unknown/source_unknown，预期2026-10-14、发布2025-12-31，与后端一致。快照来自之前三份真实UI下载/Worker/来源/raw/质量验证的受控生产操作，不是新鲜度UI自动创建，也没有替换供应商响应。
- 1440桌面与390手机、键盘读取、日期/未验证及阈值、axe0、页面无横向溢出，截图已人工观察。最终手机截图实际旧记录已过900秒，显示过期/待核验与历史原因，日期字段按单列排列。
- 首轮在实际未过期快照上使用受控浏览器clock快进，面板显示过期；服务端checked_at不改。此为浏览器时间控制，不冒称后端定时过期/告警已发生。
- 中断仅此只读server action请求，保留旧记录但显示读取失败/待核验；恢复网络后重读成功。它是受控浏览器传输故障，不等于真实供应商宕机或collector停机测试。
- 真实600519无快照，显示暂无记录且无伪造详情；刷新页面重新读取仍是同一checked_at，没有触发新探测。匿名401、跨空间403/404；只读主体可读，无新增写操作。

新脚本首次写错工作目录，文件未创建、未执行测试；使用绝对路径修正后两轮完整部署UI通过。未将准备错误计通过。既有1/5/20任务的原发布/代表图表及刷新再次通过。

命令：`.venv/bin/python -m pytest tests/test_freshness_bridge.py tests/test_research_bridge.py tests/test_collector_http.py`；在apps/web执行 `node tests/verification-freshness-ui.mjs`、`QTRADE_BASELINE_PHASE=retest node tests/verification-collection-baseline-recovery-ui.mjs`。私有截图和结果无登录凭证，server actions保留cookie在服务端。

## 仍需完成

接入持续、并发安全且有界的真实证据刷新及较新checkpoint合并；处理来源实际更新、采集/质量/发布故障、因子/研究稀疏规则。持久outbox与既有告警的过期→去重→恢复、撤权/重启及真实服务故障UI仍须验收。当前只读界面的分组通过不完成8.3完整范围，也不代替六主业务全矩阵。
