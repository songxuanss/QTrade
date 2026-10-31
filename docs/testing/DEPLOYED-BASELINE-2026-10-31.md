# Ubuntu 24 实际部署基线（2026-10-31）

## 环境

- Docker `29.1.3`、Compose `2.40.3`，daemon 明确配置 HTTP/HTTPS 代理；Docker Hub 拉取验证通过。
- 依赖容器：PostgreSQL 17、TimescaleDB 2.30.2-pg17（固定 digest）、Valkey 8、独立构建 MinIO，全部 healthy。
- 独立 collector 容器及其 PostgreSQL healthy，监听 `127.0.0.1:18100`。
- 原生身份/API/业务 Worker/生产 Web：SSO `3121`、API `3122`、Worker `3123`、Web `3120`，健康检查全部 200。
- 验证库由 `verification-local` 初始化并应用业务/行情/采集迁移；登录凭据仅保存在 Git 忽略的私有文件。

## 真实部署验收结果

- `verification-review.mjs`：**PASS**。真实 SSO 登录、真实 API/PostgreSQL、策略创建、非法/合法参数校验、v2 持久化，以及全部主页面桌面 1440 和手机 390 截图、axe、无横向溢出。
- `verification-collector-access.mjs`：**PASS，3 checks**。从导航进入及直接打开 `/workspace/data`、刷新、桌面/手机无溢出；真实数据库使会话过期后经 SSO 重新认证并返回采集页；独立 collector 显示“远程服务已连接”。
- 实际数据页截图：`.tools/verification-local/review/data-desktop.png`、`data-mobile.png`。配置供应商 Licence 后来源显示为可配置状态，页面仍保持连接。
- 新 Licence 下真实 UI 选择 `mairui.history.bars`、`601288.SH`、2025-09-01 至 2025-09-05，完成预览、提交、真实来源请求、Worker 处理、质量门禁、发布和消费者导入：
  - Job `b6f8986f-c1c5-48c2-b82d-6799173a207b`，状态 `completed`；
  - Publication `ec9a66fd-bac1-5a42-bf62-5cf81ef6064c`；
  - 标准记录 **5**、原始记录 **5**，consumer `visible`；
  - 冻结数据集 `ec9a66fd-bac1-5a42-bf62-5cf81ef6064c` 在农业银行日线图表显示 5 根 K 线；
  - 重新打开结果 URL 后任务、分段、质量入口和发布仍可见，桌面/手机无横向溢出。
- 更新为 Pro Licence 后，启用 `COLLECTOR_MAIRUI_PRO_ENABLED=true` 并实测 `mairui.pro.bars` 5 分钟：
  - Job `caa9987b-e055-4c5b-bf00-8f0392c4303e`，状态 `completed`；
  - Publication `a6c0926c-4855-5b88-bd62-2a7d9e237dc9`；
  - 标准记录 **240**、原始记录 **240**，consumer `visible`；
  - 冻结数据集在农业银行 5 分钟图表显示 240 根 BAR；
  - 重新打开结果 URL 后任务、分段、质量入口和发布仍可见，桌面/手机无横向溢出。
- 新 Licence 下背景元数据刷新返回 200、响应 11,932 字节；普通历史与 Pro 分钟两条固定行情路径均已真实通过。
- 市场终端“已下载数据”真实列出 `601288.SH` 的 5 分钟和日线冻结入口；无需加入自选或输入代码，点击 5 分钟入口直接打开 240 根冻结图表，桌面/手机无横向溢出。
- 组合范围实测：自定义池含 `600036.SH`、`601288.SH`，手工追加 `600519.SH`；UI 显示“1 个股票池、1 个手工标的，去重后 3 个标的”，预览并提交后 Job `9d9896e5-65e8-44ba-b25c-fb36b766972a` 生成 3 个分段、3 个 completed、0 失败。三个标的各 5 条 RAW/发布，验证行业池与公司组合不是单选互斥。
- 以上样本覆盖单标的、多标的与组合范围，不代替默认一年、完整股票池历史成分、长范围、重试、自动更新和完整新鲜度矩阵。

## 未完成与阻塞

- 因子历史尚未执行真实 UI 下载与浏览；目录元数据现状为 200，但不能由目录数量推导历史权限或覆盖。
- 普通历史日线只验证了 601288.SH 五日；默认一年、多标的、股票池、长范围/分段、重复补缺、部分失败、重试与取消尚未在本部署执行。
- 质量门禁、消费者和冻结图表已真实通过普通历史与 Pro 样本；不能据此宣称所有类别或自动调度完成。
- 不得用页面已连接、健康检查或受控 fixture 替代真实来源验收；OpenSpec Goal 完成数保持 **19/42**。
