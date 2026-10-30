import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
const read = (path) => JSON.parse(readFileSync(`../../.tools/${path}`));
const data = read("verification-local/review/freshness-delivery-results.json");
const admin = read("sso-local/state.json");
const reader = read("verification-local/approver.json");
const origin = "http://127.0.0.1:13000";
const browser = await chromium.launch();
const checks = [];
try {
  for (const role of ["admin", "reader"]) {
    const context = await browser.newContext({
      viewport:
        role === "admin"
          ? { width: 1440, height: 1000 }
          : { width: 390, height: 844 },
    });
    const page = await context.newPage();
    console.log(`Checking ${role} login and alert UI`);
    await page.goto(`${origin}/workspace/alerts`);
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
      (u) => ["/workspace/alerts", "/auth/consent"].includes(u.pathname),
      { timeout: 90000 },
    );
    if (page.url().includes("/auth/consent"))
      await page.getByRole("button", { name: "同意授权", exact: true }).click();
    await expect(page).toHaveURL(`${origin}/workspace/alerts`, {
      timeout: 90000,
    });
    if (!(await page.getByLabel("切换工作区", { exact: true }).isVisible()))
      await page
        .getByRole("button", { name: "打开主导航", exact: true })
        .click();
    await page
      .getByLabel("切换工作区", { exact: true })
      .selectOption(data.scope_id);
    await page.getByRole("button", { name: "切换", exact: true }).click();
    await expect(page).toHaveURL(`${origin}/`, { timeout: 60000 });
    const api = `http://127.0.0.1:3122/api/v1/workspaces/${data.scope_id}/alert-rules/events`;
    for (const event of data.events) {
      await page.goto(`${origin}/workspace/alerts?event_id=${event.event_id}`);
      const title = event.type.endsWith("recovered")
        ? "数据新鲜度恢复 · 601288.SH"
        : "数据新鲜度异常 · 601288.SH";
      await expect(
        page.getByRole("heading", { name: title, exact: true }),
      ).toBeVisible();
      const response = await page.request.get(`${api}/${event.event_id}`);
      assert.equal(response.status(), 200);
      const saved = await response.json();
      assert.equal(saved.source_event_id, event.source_event_id);
      assert.equal(
        saved.context_snapshot.collection_freshness.episode_id,
        data.notices[0].episode_id,
      );
      await expect(page.locator(".alert-detail-panel")).toContainText(
        "verification.freshness.delivery",
      );
      await page.reload();
      await expect(
        page.getByRole("heading", { name: title, exact: true }),
      ).toBeVisible();
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      );
      assert.equal(
        (await new AxeBuilder({ page }).include(".alert-center-page").analyze())
          .violations.length,
        0,
      );
    }
    await page.screenshot({
      path: `../../.tools/verification-local/review/freshness-alerts-${role}.png`,
    });
    assert(
      [403, 404].includes(
        (
          await page.request.get(
            api.replace(data.scope_id, "00000000-0000-4000-8000-000000000001"),
          )
        ).status(),
      ),
    );
    checks.push(
      `${role}: controlled producer, actual alert UI, linked episode, refresh, no overflow, axe0, cross-scope rejection`,
    );
    await context.close();
  }
  writeFileSync(
    "../../.tools/verification-local/review/freshness-alerts-ui-results.json",
    JSON.stringify(
      { checks, controlled_producer: true, real_vendor_outage: false },
      null,
      2,
    ),
    { mode: 0o600 },
  );
  console.log(
    "PASS controlled freshness delivery: real alert UI admin/reader desktop/mobile and refresh",
  );
} finally {
  await browser.close();
}
