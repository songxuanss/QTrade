# 数据工作台 UI/UX 审核（2026-10-31）

## 结论与证据边界

本轮完成了数据工作台的桌面/手机视觉、键盘、焦点、状态语义和失败草稿保护审核，并修复了会误导业务判断或丢失输入的问题。浏览器验收使用真实 Next.js 生产构建、真实 QTrade BFF/会话/权限代码和受控 OIDC/Collector fixture；**fixture 不计作真实供应商接入，也不替代部署 API/Worker/PostgreSQL/来源主链**。

当前环境没有 Docker、本地 `.env` 或已授权 MaiRui 运行配置，真实 13000/3120 部署和 298 个外部集成跳过项仍未执行。因此本文不改变 Goal 19/42 完成计数。

## 已修复

| 问题 | 处理 | 验证 |
| --- | --- | --- |
| 手机菜单点击导航后不跳转 | 不再在 Next Link 导航前同步卸载抽屉；补齐抽屉 dialog 语义、背景 inert、Tab 焦点循环、Escape 焦点恢复 | Playwright 手机导航、焦点和视觉基线通过 |
| 命令面板只有 dialog 外观 | 输入框 combobox/listbox 关系、`aria-activedescendant`、活动项滚动到可见、Tab 约束、关闭恢复触发按钮 | Auth Playwright 键盘用例通过 |
| 下载接受显示成绿色“已完成” | 下载提交使用信息色“任务已提交，等待 Worker”；完成仍以任务/RAW/发布为准 | 状态语义和实心状态色；工作台浏览器用例通过 |
| 手工标的与股票池同时选择的范围歧义 | 字段旁持续显示“当前生效：手工标的 / 股票池 / 尚未选择” | 组件单元回归及视觉基线 |
| queued 任务不刷新、选择器状态陈旧、慢响应覆盖新选择 | queued 纳入轮询；串行退避；读取任务同步列表；请求代次阻止旧响应覆盖；失败保留旧快照 | 4 个组件单元用例，含竞态和退避 |
| 刷新数据丢失表单草稿 | `router.refresh()` 更新服务端结果，挂载中的客户端表单保持；拒绝下载只回填标的与日期，不回填授权字段 | Auth Playwright 桌面/手机刷新与拒绝恢复 |
| 旧日线/分钟入口与新分区职责冲突 | 折叠为明确“旧版兼容入口”，逐笔归档保持当前分区 | 桌面/手机视觉及键盘 |
| 业务文字过小、输入边界和焦点弱 | 打包 IBM Plex Sans；业务辅助文字提升至 11–12px、表单 12–13px；控制边界对比度 3.17:1；实心 2px 键盘焦点 | 截图人工审核、axe 0、无横向溢出 |
| 状态色不完整/正常等待误用红色 | queued 信息色、waiting/部分缺口警告色、failed 危险色、no_data/cancelled 中性色；文字与颜色同时存在 | 运行结果截图、状态文案断言 |

旧版兼容入口默认折叠，这不是隐藏错误：新采集统一从“行情与因子”进入，旧任务仍可在“逐笔与兼容”和“数据资产”定位。

## 自动化结果

- Python 全量：Ruff、mypy、架构检查通过，**1262 passed / 298 skipped**。
- 前端检查：ESLint、Prettier、TypeScript、**67 Vitest passed**。
- 生产构建：Next.js 16.4.3 构建通过；生产依赖审计 **0 vulnerabilities**。
- Auth Playwright：**14 passed**，包含工作台、策略版本、回测、告警生命周期、运行控制、命令面板键盘流程和数据工作台桌面/手机视觉。
- 公共 Playwright：**7 passed**，覆盖桌面/390px 视觉、移动导航焦点、404、axe 和 HttpOnly 会话隐私。
- axe：数据初始配置、结果桌面和 390px 手机均为 0 violations；页面无整体横向溢出。

视觉基线：

- `apps/web/tests/auth/data-workbench.spec.ts-snapshots/data-collect-desktop-chromium-linux.png`
- `data-collect-mobile-chromium-linux.png`
- `data-results-desktop-chromium-linux.png`
- `data-results-mobile-chromium-linux.png`

结果页基线包含 running、预算 waiting 和 failed 三种状态；日期、任务和名称为受控固定值。动态 ID 仅来自 fixture，不包含凭证。

## 主业务覆盖矩阵

| 流程 | 本轮证据 | 状态 |
| --- | --- | --- |
| 登录/权限 | 真实会话/BFF 代码 + 受控 OIDC；角色、伪造注销、state、跨作用域拒绝 | UI 与拒绝路径通过；真实部署待复核 |
| 策略版本 | 生产 UI 创建策略、校验 schema、保存 v2、刷新持久化；静态检查拒绝 | fixture 通过；真实业务库重启验收未重跑 |
| 数据采集 | 数据页配置/结果/状态/键盘/手机；真实来源与 Worker 前置配置缺失 | UI 通过；真实来源闭环阻塞 |
| 回测/报告 | Auth 回测取消/进度/完成持久化用例通过 | fixture 通过；真实引擎未就绪 |
| 告警 | 规则版本、非法表达式、认领→确认→静默→备注→关闭、查看者写入拒绝 | fixture 通过；实时行情事实未接入 |
| 已连接交易控制 | 运行控制需要明确确认后才能停止；实盘继续关闭 | fixture 通过；无真实 broker，不声称实盘 |

## 仍待完成

1. 在具备 Docker、PostgreSQL/TimescaleDB、MinIO、真实 SSO/API/Worker 和 MaiRui 授权的实际部署执行 `USER-VERIFICATION.md` 数据采集流程，并核对非空 RAW、质量门禁、发布、图表和刷新。
2. 298 个跳过集成测试不是通过；需在有外部依赖的环境运行。
3. 真实来源故障、授权撤销、重启补跑、自动更新与新鲜度告警仍需按 Goal 台账逐项验收。
4. 数据页视觉基线来自受控状态投影，不能代替真实业务数据的可读性和极值溢出验证。
