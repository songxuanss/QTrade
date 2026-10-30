import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
const read = (path) => JSON.parse(readFileSync(`../../.tools/${path}`));
const baseline = read("verification-local/review/collection-retest-ui.json");
const admin = read("sso-local/state.json");
const reader = read("verification-local/approver.json");
const run = baseline.runs[0],
  publication = run.parts[0].publication_id;
const origin = "http://127.0.0.1:13000";
const api = `http://127.0.0.1:3122/api/v1/workspaces/${baseline.scope_id}/data/collector`;
const address = `${origin}/workspace/data?collector_job=${run.job_id}`;
const browser = await chromium.launch(),
  checks = [];
try {
  for (const role of ["admin", "reader"]) {
    const context = await browser.newContext({
      viewport:
        role === "admin"
          ? { width: 1440, height: 1000 }
          : { width: 390, height: 844 },
    });
    const page = await context.newPage();
    await page.goto(`${origin}/workspace/data`, { timeout: 90000 });
    await page
      .getByRole("textbox", { name: "邮箱", exact: true })
      .fill(
        role === "admin" ? "qtrade-integration@example.test" : reader.email,
      );
    await page
      .getByLabel("密码", { exact: true })
      .fill(role === "admin" ? admin.password : reader.password);
    await page.getByRole("button", { name: "登 录", exact: true }).click();
    await page.waitForURL(
      (u) => ["/workspace/data", "/auth/consent"].includes(u.pathname),
      { timeout: 90000 },
    );
    if (page.url().includes("/auth/consent"))
      await page.getByRole("button", { name: "同意授权", exact: true }).click();
    await expect(page).toHaveURL(`${origin}/workspace/data`, {
      timeout: 90000,
    });
    if (!(await page.getByLabel("切换工作区", { exact: true }).isVisible()))
      await page
        .getByRole("button", { name: "打开主导航", exact: true })
        .click();
    await page
      .getByLabel("切换工作区", { exact: true })
      .selectOption(baseline.scope_id);
    await page.getByRole("button", { name: "切换", exact: true }).click();
    await expect(page).toHaveURL(`${origin}/`, { timeout: 60000 });
    await page.goto(address, { timeout: 90000 });
    const endpoint = `${api}/publications/${publication}/freshness`;
    const response = await page.request.get(endpoint);
    assert.equal(response.status(), 200);
    const initial = await response.json();
    assert.equal(initial.snapshot.status, "unknown");
    assert.equal(initial.snapshot.reason, "source_unknown");
    const root = page.locator(`[data-freshness-publication="${publication}"]`);
    await expect(root).toBeVisible({ timeout: 60000 });
    if (role === "admin") await page.clock.install();
    async function load() {
      await root
        .getByRole("button", { name: /查看新鲜度快照|刷新新鲜度快照/ })
        .focus();
      await page.keyboard.press("Enter");
      await expect(
        root.getByRole("region", { name: "数据新鲜度检查", exact: true }),
      ).toBeVisible({ timeout: 30000 });
    }
    await load();
    await expect(root).toContainText(
      initial.snapshot.expectation.complete_through,
    );
    await expect(root).toContainText("2025-12-31");
    for (const [label, value] of [
      ["来源完整周期", "未验证"],
      ["本地已采集周期", initial.snapshot.evidence.collected.complete_through],
      ["已发布周期", initial.snapshot.evidence.published.complete_through],
      ["检查有效时长", `${initial.max_age_seconds} 秒`],
    ]) {
      await expect(
        root.getByText(label, { exact: true }).locator("..").locator("dd"),
      ).toHaveText(value);
    }

    await expect(root).toContainText("尚无来源最新观测");
    await expect(root).toContainText("刷新快照仅重新读取记录，不触发来源检查");
    assert.equal(
      (
        await new AxeBuilder({ page })
          .include(`[data-freshness-publication="${publication}"]`)
          .analyze()
      ).violations.length,
      0,
    );
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    await root
      .getByRole("button", { name: "刷新新鲜度快照", exact: true })
      .focus();
    await page.screenshot({
      path: `../../.tools/verification-local/review/freshness-${role}.png`,
      fullPage: false,
    });
    if (role === "admin") {
      // Controlled browser time and an aborted read; real API/PG data stay intact.
      await page.clock.fastForward((initial.max_age_seconds + 2) * 1000);
      await expect(root.getByRole("status")).toHaveText(
        "检查记录已过期，当前状态待核验",
      );
      await page.clock.resume();
      const match = "**/workspace/data?collector_job=*";
      await page.route(match, async (route) => {
        if (
          route.request().method() === "POST" &&
          route.request().headers()["next-action"] &&
          (route.request().postData() ?? "").includes(publication)
        )
          return route.abort();
        return route.continue();
      });
      await root
        .getByRole("button", { name: "刷新新鲜度快照", exact: true })
        .click();
      await expect(root.getByRole("alert")).toContainText("当前状态待核验", {
        timeout: 30000,
      });
      await expect(root.getByRole("status")).toHaveText(
        "读取或核验失败，当前状态待核验",
      );
      await page.unroute(match);
      await load();
      await expect(root.getByRole("alert")).toHaveCount(0);
    }
    let persistedCheck = initial.snapshot.checked_at;
    const command = root.getByRole("button", {
      name: "重新核验新鲜度",
      exact: true,
    });
    const session = await (
      await page.request.get("http://127.0.0.1:3122/auth/session")
    ).json();
    assert.equal((await page.request.post(`${endpoint}/check`)).status(), 403);
    if (role === "admin") {
      await expect(command).toBeVisible();
      // Abort only this command's browser transport. It must preserve the
      // saved check and mark the current UI state as requiring verification.
      const match = "**/workspace/data?collector_job=*";
      await page.route(match, async (route) => {
        if (
          route.request().method() === "POST" &&
          route.request().headers()["next-action"] &&
          (route.request().postData() ?? "").includes(publication)
        )
          return route.abort();
        return route.continue();
      });
      await command.click();
      await expect(root.getByRole("alert")).toContainText("当前状态待核验", {
        timeout: 30000,
      });
      assert.equal(
        (await (await page.request.get(endpoint)).json()).snapshot.checked_at,
        initial.snapshot.checked_at,
      );
      await page.unroute(match);
      await command.focus();
      await page.keyboard.press("Enter");
      await expect(command).toBeEnabled({ timeout: 90000 });
      await expect(root.getByRole("alert")).toHaveCount(0, { timeout: 90000 });
      await expect
        .poll(
          async () => {
            const saved = await (await page.request.get(endpoint)).json();
            return saved.snapshot.checked_at;
          },
          { timeout: 90000 },
        )
        .not.toBe(initial.snapshot.checked_at);
      const saved = await (await page.request.get(endpoint)).json();
      assert.equal(saved.snapshot.status, "unknown");
      assert.equal(saved.snapshot.evidence.source, null);
      assert.equal(
        saved.snapshot.evidence.published.complete_through,
        "2025-12-31",
      );
      persistedCheck = saved.snapshot.checked_at;
      await expect(root).toContainText("证据不足，状态未知");
      checks.push(
        "admin: keyboard manager check through deployed collector/calendar/archive/PG; old history stays unknown",
      );
    } else {
      await expect(command).toHaveCount(0);
      assert.equal(
        (
          await page.request.post(`${endpoint}/check`, {
            headers: { "X-CSRF-Token": session.csrf_token },
          })
        ).status(),
        403,
      );
      checks.push(
        "reader: command hidden and real CSRF-authenticated API denied",
      );
    }
    assert(
      [403, 404].includes(
        (
          await page.request.post(
            `${endpoint.replace(baseline.scope_id, "00000000-0000-4000-8000-000000000001")}/check`,
            { headers: { "X-CSRF-Token": session.csrf_token } },
          )
        ).status(),
      ),
    );
    await page.reload({ timeout: 90000 });
    await expect(root).toBeVisible({ timeout: 60000 });
    await load();
    assert.equal(
      (await (await page.request.get(endpoint)).json()).snapshot.checked_at,
      persistedCheck,
    );
    assert(
      [403, 404].includes(
        (
          await page.request.get(
            endpoint.replace(
              baseline.scope_id,
              "00000000-0000-4000-8000-000000000001",
            ),
          )
        ).status(),
      ),
    );
    const anonymous = await browser.newContext();
    assert.equal((await anonymous.request.get(endpoint)).status(), 401);
    await anonymous.close();
    if (role === "admin") {
      const otherRun = baseline.runs[2],
        other = otherRun.parts.find((p) => p.instrument_id === "600519.SH");
      assert(other);
      await page.goto(
        `${origin}/workspace/data?collector_job=${otherRun.job_id}`,
        { timeout: 90000 },
      );
      const missing = page.locator(
        `[data-freshness-publication="${other.publication_id}"]`,
      );
      await missing
        .getByRole("button", { name: "查看新鲜度快照", exact: true })
        .click();
      await expect(missing.getByRole("alert")).toContainText(
        "暂无新鲜度检查记录",
        { timeout: 30000 },
      );
      await expect(
        missing.getByRole("region", { name: "数据新鲜度检查", exact: true }),
      ).toHaveCount(0);
    }
    checks.push(
      `${role}: real scoped snapshot, keyboard, axe0, no overflow, immutable refresh, anonymous/cross-scope reject`,
    );
    await context.close();
  }
  writeFileSync(
    "../../.tools/verification-local/review/freshness-ui-results.json",
    JSON.stringify(
      {
        checks,
        controlled:
          "browser clock expiry and aborted read action; no fake supplier results",
        integration:
          "real SSO/UI/API/collector/PG, manager-triggered calendar/archive check and immutable snapshot; automatic refresh/outbox not connected",
      },
      null,
      2,
    ),
    { mode: 0o600 },
  );
  console.log(
    "PASS freshness UI: real admin/reader, desktop/mobile, expiry/failure/absence/recovery and refresh",
  );
} finally {
  await browser.close();
}
