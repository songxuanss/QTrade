<!-- BEGIN:nextjs-agent-rules -->

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

# Playwright 截图快照再生成

提交在库里的快照生成于指定交付主机(Ubuntu 24 WSL)的**封闭字体环境**:fontconfig 只可见 `.tools/snapshot-fonts/` 里 sha256 锁定的字体字节(Noto Sans CJK + Liberation),CI 与本地渲染同一份字节,快照比对在两边都是真实门禁。再生成必须复用同一环境,否则全字形像素漂移会淹没真实 UI 差异:

```bash
# 一次性(或 apps/web/tests/snapshot-fonts.sha256 清单变更后)provision 锁定字体:
bash scripts/provision-snapshot-fonts.sh
cd apps/web
# auth 配置服务 production 产物(serve-production.mjs),先构建;
# e2e 默认配置(playwright.config.ts)服务 dev server(3100),无需构建
npm run build
PLAYWRIGHT_BROWSERS_PATH=<repo>/.tools/ms-playwright \
LD_LIBRARY_PATH=<repo>/.tools/browser-deps/root/usr/lib/x86_64-linux-gnu \
npx playwright test --config playwright.auth.config.ts <spec 文件> --update-snapshots
# e2e 快照同理,换默认配置即可:
# npx playwright test tests/e2e/baseline.spec.ts --update-snapshots
```

- 两个 Playwright 配置加载时调用 `tests/font-env.ts`:校验锁定字体已 provision、生成只含 `.tools/snapshot-fonts/` 的 fontconfig(含显式 generic 族别名)并注入 `FONTCONFIG_FILE`,不再需要手工设置;系统字体与 `/mnt/c/Windows/Fonts` 一律不参与快照渲染。
- CJK 走应用 CSS 字体栈的首选 `Noto Sans CJK SC`。旧基线曾用 Windows 挂载的微软雅黑,任何 Linux runner 都无法合法提供该字体,这是 2026-11 之前 CI 像素失败的根因。
- CI(ubuntu-24.04 runner)在 e2e/auth 前执行 `scripts/provision-snapshot-fonts.sh` 下载同一份锁定字体(archive.ubuntu.com,sha256 校验失败即红);阈值不放宽,不在 CI 上再生成快照。
- 只再生成受当前变更影响的快照;再生成前先用 diff 图确认失配是预期 UI 变化还是环境漂移。
