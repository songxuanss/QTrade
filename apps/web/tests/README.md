# 前端测试

单元测试放在 `src/**/*.test.ts(x)` 或 `tests/unit/**/*.test.ts(x)`。
端到端测试放在 `tests/e2e/**/*.spec.ts`，由 Playwright 自动启动本地 Web 服务。
当前已有基础页面安全、404 与 axe 可访问性测试；无测试或测试失败均退出非零。

```bash
npm run test
npx playwright install --with-deps chromium
npm run test:e2e
```

UI 基线覆盖桌面/390px 手机视觉快照、导航与 Escape、键盘跳转主内容、权限占位、
空态/加载/错误组件及 axe 可访问性检查。快照提交到 Git，日常执行不得更新基线。
有意调整设计时运行 `npm run test:e2e -- --update-snapshots`，逐张人工审阅后再提交。
使用 lockfile 中固定的 Chromium 版本；Linux 环境需安装 Noto CJK 字体以稳定中文渲染。

缺失页面也有视觉基线与返回导航测试；无效工作区 section 在流式渲染前返回真实 404。
Compose 启动后运行 `npm run test:production`，检查生产 UI、桌面/手机可访问性、真实 404、
开发预览隔离与无浏览器运行异常；可用 `QTRADE_TEST_WEB_ORIGIN` 指定测试环境。

会话隐私 E2E 使用 `tests/fixtures/session-browser.py`，因此需要先从根目录 `uv sync --locked`。
该 loopback fixture 使用真实 SessionService/Cookie/HTTP 接口与测试身份，不包含在生产镜像。
检查 HttpOnly 在浏览器中的保护、响应/storage 无 OIDC token、伪造 logout 拒绝与撤销生效。

OIDC 集成测试运行 `npm run build && npm run test:auth`，使用生产 Next.js 和 loopback
synthetic provider/API fixture。覆盖保护页登录、签名/缺失 state 拒绝、token 隐私、
正常退出/伪造退出和登录页面 axe 检查。两组 Playwright 串行运行；协议负例在单元测试中覆盖。

生产登录态另有桌面与 390px 手机视觉基线（tests/auth），覆盖真实 API 返回的 Workspace
名称/本地角色和连接状态。新基线须人工查看；日常 `npm run test:auth` 仅做比较。
