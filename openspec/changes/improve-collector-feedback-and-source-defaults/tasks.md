# Tasks

## 1. 采集出口代理透传

- [x] 1.1 在 `compose.collector.yaml` 为 collector 服务注入标准代理变量（运行时 environment + 构建期 build args，默认空、NO_PROXY 默认回环），在 `.env.collector` 写入本地代理地址（不入库）；用 `docker compose up -d --build collector` 重建后验证容器内 httpx 经代理访问 MaiRui 返回业务状态码、`/ready` 健康检查 200、authorizer 回环调用不经代理。

## 2. 契约与适配器 tier 字段

- [x] 2.1 `SourceDataset` 增加 `tier: Literal["paid","free"] = "free"`；MaiRui adapter 将 `mairui.pro.bars`、`mairui.factor.history` 标记 paid、`mairui.history.bars` 标记 free；运行契约与采集相关 pytest 定向用例确认向后兼容。（136 passed；ruff/format/mypy 通过）

## 3. Web 来源默认优先级

- [x] 3.1 `collector-types.ts` 增加 `tier?: "paid" | "free"`；默认来源选择改为「configured ∧ 支持当前 kind+period ∧ 付费优先」，保存偏好显式来源不被覆盖；来源下拉标注付费/免费。
- [x] 3.2 周期变化时若当前来源不支持新周期，自动切换到支持它的最优来源（付费优先）；用 Vitest 单元用例覆盖付费优先、日线回退、偏好优先三条场景。（collector-panel 12/12，web 全量 86/86）

## 4. Web 反馈就近可见

- [x] 4.1 表单页脚新增内联错误提示（与顶部横幅同源 state，role=alert），错误出现时 scrollIntoView；「使用最近自然年日期」busy 期间显示「计算中…」；禁用按钮附近展示原因（已有日期 / 来源未配置）。
- [x] 4.2 更新受影响的 web 单元/验证脚本断言（来源下拉文案、按钮状态），运行 Vitest 与定向 .mjs 验证。（单元断言已过；.mjs 无 label/文案依赖，待真实栈复跑）

## 5. 交付验证

- [x] 5.1 重建并重启本地真实栈，登录后 Playwright 复测：自然年按钮填充日期、预览出计划、错误就近可见（模拟失败路径）、warm/cold 首屏时间记录进证据；桌面与移动视口各截图。证据（`.tools/verification-local/ux-audit/uxfix-*.png` + 脚本输出）：付费优先默认（1d→免费源、选项带付费/免费标注；切 5m 自动换 Pro 付费源、回 1d 回退免费源）；周期下拉为跨源并集 1d/1h/30m/15m/5m/1m；「计算中…」busy 标签观测为真、约 3s 填充；填充后按钮禁用且就近给出原因；失败路径以 collector 出口代理指向死端口模拟，页脚内联「来源请求超时，任务会按退避计划重试。」在视口内且全页唯一 alert；移动 390px 无横向溢出。首屏 cold DCL 8.8s / warm 4.5s（较修复前 14.6s 下降；剩余为 WSL2 挂载盘 Next.js 冷启动，属环境固有）。
- [x] 5.2 运行全量门禁（pytest 136、Vitest collector-panel 12/12 与 web 全量 86/86、mypy、ruff check/format、eslint、tsc、prettier、openspec strict、constitution、architecture 全过），审查 staged diff 无凭据（仅 docstring 与 `${VAR}` 占位命中）后按里程碑提交；推送受凭据 scope 限制时如实披露。
