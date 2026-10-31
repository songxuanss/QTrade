# 基线恢复与验证环境（2026-10-31）

## 范围与结论

本轮先恢复可复核的源码基线，再继续专业数据工作台 Goal。此处只记录已经执行的检查，不把跳过项、fixture 或页面健康状态解释为真实来源和完整部署验收通过。

上一会话留下 `f549b62` 初始源码快照。首轮 Python 检查为 **1230 passed / 298 skipped / 11 failed**；失败包括 8 个真实 TCP/spawn 用例、研究查询中央授权守卫、因子回执选择歧义和结构化日志断言。前端静态检查及生产构建通过，但移动导航浏览器用例失败。

## 已执行修复

- 研究查询 POST 路由接入中央 RBAC/CSRF guard，并更新角色、跨身份及不调用下游的拒绝测试。
- 因子测试明确区分 `http_response` 与 `factor_history` 两类不可变证据，避免文件枚举顺序造成假失败；两类回执都必须存在。
- 日志断言使用标准 `LogRecord.getMessage()`，不再依赖 formatter 动态属性。
- 三个采集后台开关统一只接受精确 `true` / `false`，非法配置在装配时拒绝；显式停用 Worker 不在 `/ready` 伪造周期故障。
- 真实 TCP fixture 显式绕过 loopback 代理，防止代理在无响应场景合成 HTTP 503，确保测试观察的是实际 socket 行为。
- 修复手机 Next Link 在抽屉提前关闭时不导航的问题；增加抽屉焦点约束、背景 inert、Escape 焦点恢复及浏览器回归。

相关运维语义已同步到 `docs/operations/independent-collector.md`。

## 当前可复核结果

### Linux 原生文件系统

- 完整 Python 检查：Ruff、mypy、架构检查通过，**1262 passed / 298 skipped**。
- 后台开关与真实 TCP 定向回归：**15 + 12 passed**。
- 前端基线：ESLint、Prettier、TypeScript、**67 个 Vitest** 和生产构建通过。
- Playwright 公共/会话基线：**7 passed**，覆盖桌面与 390px 手机视觉、移动导航、键盘焦点、404、axe 和 HttpOnly 会话隐私。
- Auth Playwright：**14 passed**，包含工作台、策略版本、回测、告警、运行控制、命令面板和数据工作台桌面/手机视觉；受控 OIDC/Collector fixture 不计真实供应商。
- 生产依赖 `npm audit --omit=dev`：**0 vulnerabilities**；Next.js 升级到 16.4.3，Monaco 的 DOMPurify 通过锁文件覆盖到 3.4.17。

### WSL `/mnt/c` 与代理边界

9p 挂载中的 spawn 子进程可停在 `p9_client_rpc`，使 5 秒整体截止先于 Python 启动完成；这不是可以通过删除截止或改成不回收进程的测试来规避的产品缺陷。权威检查因此在 Ubuntu 24 的 Linux 原生 ext4 隔离副本中使用同一锁文件运行，不把 `/mnt/c` 的环境超时写成业务通过。

宿主 Windows 风格 `NO_PROXY=127.*` 不被当前 HTTPX 作为精确 loopback 排除项可靠识别。测试 fixture 现在只为 `127.0.0.1,localhost` 设置明确 bypass；没有关闭生产代理能力，也没有把代理合成响应当成来源证据。

## 尚未完成的验收

- 298 个跳过项仍需在具备 Docker、PostgreSQL/TimescaleDB、MinIO、真实身份/API/Worker 的部署中执行；跳过不等于通过。
- 当前 Ubuntu 24 没有 Docker，源码快照没有本地 `.env`、已保存身份或供应商运行配置；真实部署和 MaiRui live 主链尚不能在本轮环境中复核。
- 数据工作台完整业务矩阵、真实来源非空 RAW→质量→发布→消费者刷新，以及 OpenSpec 其余未完成任务继续保持原状态。本文不提升 Goal 的 19/42 完成计数。
