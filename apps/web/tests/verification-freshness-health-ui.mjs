import { openDataView } from "./data-workbench-navigation.mjs";
/** Real deployed read path, persisted publications and disabled watch inventory.
 * Controlled transport failure is separate from supplier availability evidence.
 * No price requests, enabled schedules, or fabricated HTTP responses are used.
 */
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const read = (path) => JSON.parse(readFileSync(`../../.tools/${path}`));
const baseline = read("verification-local/review/collection-retest-ui.json");
const admin = read("sso-local/state.json");
const reader = read("verification-local/approver.json");
const origin = "http://127.0.0.1:13000";
const root = `http://127.0.0.1:3122/api/v1/workspaces/${baseline.scope_id}/data/collector`;
const endpoint = `${root}/freshness/health`;
const browser = await chromium.launch();
const checks = [];
const created = [];
let expectedKeys;
try {
  for (const role of ["admin", "reader"]) {
    const context = await browser.newContext({
      viewport:
        role === "admin"
          ? { width: 1440, height: 1000 }
          : { width: 390, height: 844 },
    });
    const page = await context.newPage();
    console.log(`Checking ${role} health UI`);
    await page.goto(`${origin}/workspace/data`);
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
    await page.goto(`${origin}/workspace/data`);
    await openDataView(page, "来源与新鲜度", "数据新鲜度");

    if (role === "admin") {
      const session = await (
        await page.request.get("http://127.0.0.1:3122/auth/session")
      ).json();
      // Populate only stopped configurations for real archived 20-stock data in
      // this dedicated validation scope. Never stop or adopt an existing watch.
      for (const part of baseline.runs[2].parts) {
        const url = `${root}/publications/${part.publication_id}/freshness/watch`;
        const existing = await page.request.get(url);
        if (existing.status() === 404) {
          const response = await page.request.post(url, {
            headers: { "X-CSRF-Token": session.csrf_token },
          });
          assert.equal(response.status(), 200);
          const watch = await response.json();
          assert.equal(watch.enabled, false);
          created.push(watch.watch_id);
        } else assert.equal(existing.status(), 200);
      }
    }
    const response = await page.request.get(endpoint + "?limit=100");
    assert.equal(response.status(), 200);
    assert.equal(response.headers()["cache-control"], "no-store");
    const before = await response.json();
    const keys = before.entries.map((e) => e.key_sha256);
    assert(keys.length > 20 && keys.length <= 100 && !before.next_key);
    if (role === "admin") expectedKeys = keys;
    else assert.deepEqual(keys, expectedKeys);
    assert(before.entries.some((e) => e.instrument_name === "农业银行"));
    assert(before.entries.some((e) => e.effective_status === "unchecked"));
    for (const entry of before.entries.filter((e) =>
      e.key.source.startsWith("verification."),
    ))
      assert.equal(entry.effective_status, "source_unconfigured");
    const health = page.locator("[data-freshness-health]");
    await health.locator(":scope > summary").focus();
    await page.keyboard.press("Enter");
    await expect(health.getByRole("status")).toContainText("第 1 页", {
      timeout: 60000,
    });
    const cardKeys = () =>
      health
        .locator("[data-health-key]")
        .evaluateAll((nodes) => nodes.map((n) => n.dataset.healthKey));
    const first = await cardKeys();
    assert.deepEqual(first, keys.slice(0, 20));
    await health
      .getByRole("button", { name: "下一页健康记录", exact: true })
      .click();
    await expect(health.getByRole("status")).toContainText("第 2 页");
    assert.deepEqual(await cardKeys(), keys.slice(20));
    await expect(
      health.getByRole("button", { name: "下一页健康记录", exact: true }),
    ).toBeDisabled();
    await health
      .getByRole("button", { name: "上一页健康记录", exact: true })
      .click();
    await expect(health.getByRole("status")).toContainText("第 1 页");
    assert.deepEqual(await cardKeys(), first);
    await expect(health).toContainText("未列出的数据并未因此验证");
    await expect(
      health.getByRole("button", { name: /启用|停用|下载|核验数据/ }),
    ).toHaveCount(0);

    // Abort only the explicit health refresh action after the page has settled.
    // Existing cards must remain reference evidence and lose any green status.
    await page.route("**/workspace/data*", async (route) => {
      if (
        route.request().method() === "POST" &&
        route.request().headers()["next-action"]
      )
        await route.abort("failed");
      else await route.continue();
    });
    await health
      .getByRole("button", { name: "刷新健康快照", exact: true })
      .click();
    await expect(health.getByRole("alert")).toContainText("读取失败");
    await expect(
      health.getByRole("button", { name: "刷新健康快照", exact: true }),
    ).toBeEnabled();
    assert.deepEqual(await cardKeys(), first);
    assert.equal(
      await health.getByText("读取失败，待核验", { exact: true }).count(),
      first.length,
    );
    await page.unroute("**/workspace/data*");
    await health
      .getByRole("button", { name: "刷新健康快照", exact: true })
      .click();
    await expect(health.getByRole("alert")).toHaveCount(0);
    await expect(health.getByRole("status")).toContainText("第 1 页");
    await page.reload();
    await health.locator(":scope > summary").click();
    await expect(health.getByRole("status")).toContainText("第 1 页");
    assert.deepEqual(await cardKeys(), first);
    const after = await (
      await page.request.get(endpoint + "?limit=100")
    ).json();
    // The read path must not create checks, change schedules, or claim events.
    const stable = (entries) =>
      entries.map((e) => ({
        key: e.key_sha256,
        snapshot: e.snapshot,
        watch: e.watch,
        consumer_state: e.consumer_state,
      }));
    assert.deepEqual(stable(after.entries), stable(before.entries));
    for (const query of ["?limit=true", "?limit=101", "?after_key=bad"])
      assert.equal((await page.request.get(endpoint + query)).status(), 422);
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
    assert.equal(
      (
        await new AxeBuilder({ page })
          .include("[data-freshness-health]")
          .analyze()
      ).violations.length,
      0,
    );
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    // A long mobile inventory is taller than the viewport. Align its header,
    // rather than centering a random middle card in the review screenshot.
    await health.evaluate((node) => node.scrollIntoView({ block: "start" }));
    await page.screenshot({
      path: `../../.tools/verification-local/review/freshness-health-${role}.png`,
    });
    checks.push(
      `${role}: scoped persisted inventory, exact two-page navigation, keyboard, reload, controlled read failure/retry, no mutation, axe and overflow passed`,
    );
    await context.close();
  }
  const anonymous = await browser.newContext();
  assert.equal((await anonymous.request.get(endpoint)).status(), 401);
  await anonymous.close();
  writeFileSync(
    "../../.tools/verification-local/review/freshness-health-ui-results.json",
    JSON.stringify(
      {
        checks,
        scope_id: baseline.scope_id,
        keys: expectedKeys,
        created_disabled_watches: created,
        supplier_probe: false,
        controlled_transport_failure: true,
      },
      null,
      2,
    ),
    { mode: 0o600 },
  );
  console.log(
    "PASS deployed health desktop/mobile read, paging, persistence and failure recovery",
  );
} finally {
  await browser.close();
}
