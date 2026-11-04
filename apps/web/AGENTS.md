<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

# Playwright 截图快照再生成

提交在库里的快照生成于指定交付主机(Ubuntu 24 WSL)的固定字体环境。再生成必须复用同一环境,否则全字形像素漂移会淹没真实 UI 差异:

```bash
cd apps/web
npm run build   # auth/e2e 配置都服务 production 产物,先构建
PLAYWRIGHT_BROWSERS_PATH=<repo>/.tools/ms-playwright \
LD_LIBRARY_PATH=<repo>/.tools/browser-deps/root/usr/lib/x86_64-linux-gnu \
FONTCONFIG_FILE=<repo>/apps/web/tests/fonts.conf \
npx playwright test --config playwright.auth.config.ts <spec 文件> --update-snapshots
```

- `tests/fonts.conf` 把 fontconfig 钉到系统字体 + `/mnt/c/Windows/Fonts`(CJK 字形来源),保证跨次运行渲染一致。
- CI runner(ubuntu-latest 已迁 Ubuntu 26)字体与本环境不同,快照比对在 runner 上可能因环境漂移失败;如实披露,不放宽阈值、不在 CI 上再生成。
- 只再生成受当前变更影响的快照;再生成前先用 diff 图确认失配是预期 UI 变化还是环境漂移。
