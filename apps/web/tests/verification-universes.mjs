import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const identity = JSON.parse(readFileSync("../../.tools/sso-local/state.json"));
const origin = "http://127.0.0.1:3120",
  api = "http://127.0.0.1:3122";
const browser = await chromium.launch();
try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  const page = await context.newPage();
  await page.goto(`${origin}/workspace/data`, { timeout: 120000 });
  await page
    .getByRole("textbox", { name: "邮箱", exact: true })
    .fill("qtrade-integration@example.test");
  await page.getByLabel("密码", { exact: true }).fill(identity.password);
  await page.getByRole("button", { name: "登 录", exact: true }).click();
  await page.waitForURL(/3120\/workspace\/data|3121\/auth\/consent/, {
    timeout: 120000,
  });
  if (page.url().includes("/auth/consent"))
    await page.getByRole("button", { name: "同意授权", exact: true }).click();
  await expect(page).toHaveURL(`${origin}/workspace/data`, { timeout: 120000 });
  const session = await (await page.request.get(`${api}/auth/session`)).json();
  const root = `${api}/api/v1/workspaces/${session.workspace_id}/data/universes`;
  const anonymous = await browser.newContext();
  assert.equal(
    (await anonymous.request.get(`${root}/snapshots`)).status(),
    401,
  );
  await anonymous.close();
  await page.getByLabel("搜索板块", { exact: true }).fill("半导体");
  await page.getByLabel("股票池类别", { exact: true }).selectOption("industry");
  await page.getByRole("button", { name: "搜索股票池", exact: true }).click();
  const resolve = page.locator('form:has(input[value="mairui:sw2_270100"])');
  await expect(resolve).toBeVisible({ timeout: 120000 });
  await resolve
    .getByRole("button", { name: "预览并保存成员", exact: true })
    .click();
  await page.waitForURL(/universe_snapshot=/, { timeout: 120000 });
  const snapshotId = new URL(page.url()).searchParams.get("universe_snapshot");
  const result = await page.request.get(`${root}/snapshots/${snapshotId}`);
  assert.equal(result.status(), 200);
  const snapshot = await result.json();
  assert.equal(snapshot.universe_id, "mairui:sw2_270100");
  assert.equal(snapshot.members.length, 177);
  assert.equal(
    new Set(snapshot.members.map((m) => m.instrument.instrument_id)).size,
    177,
  );
  assert.ok(snapshot.market_scope.includes("BJ"));
  await expect(page.locator("#universe-members")).toContainText("177 个成员");
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    assert.deepEqual((await new AxeBuilder({ page }).analyze()).violations, []);
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    await page.locator("#universe-catalog").screenshot({
      path: `../../.tools/verification-local/review/universes-${width}.png`,
    });
  }
  await page.reload();
  await expect(page.locator("#universe-members")).toContainText("177 个成员");
  const refreshed = await (
    await page.request.get(`${root}/snapshots/${snapshotId}`)
  ).json();
  assert.equal(refreshed.content_sha256, snapshot.content_sha256);
  await page.getByRole("link", { name: "下一页", exact: true }).last().click();
  await expect(page.locator("#universe-members")).toContainText("第 2 页");
  await page.goto(
    `${origin}/workspace/data?universe_search=${encodeURIComponent("沪深300")}&universe_category=index_members`,
  );
  await page
    .locator('form:has(input[value="mairui:hs300"])')
    .getByRole("button", { name: "预览并保存成员", exact: true })
    .click();
  await page.waitForURL(/universe_snapshot=/, { timeout: 120000 });
  const indexId = new URL(page.url()).searchParams.get("universe_snapshot");
  const indexSnapshot = await (
    await page.request.get(`${root}/snapshots/${indexId}`)
  ).json();
  assert.equal(indexSnapshot.members.length, 300);
  assert.equal(indexSnapshot.category, "index_members");
  assert.ok(
    indexSnapshot.members.every(
      (member) => member.instrument.instrument_kind === "stock",
    ),
  );
  await page.reload();
  await expect(page.locator("#universe-members")).toContainText("300 个成员");
  await page.getByText("创建我的股票池", { exact: true }).click();
  await page.getByLabel("股票池名称", { exact: true }).fill("UI重点股票");
  await page
    .getByLabel("股票代码", { exact: true })
    .fill("601288.SH, 600519.SH, 601288");
  await page
    .getByRole("button", { name: "保存自定义股票池", exact: true })
    .click();
  await page.waitForURL(/universe_snapshot=/, { timeout: 120000 });
  await expect(page.locator("#universe-members")).toContainText("2 个成员", {
    timeout: 120000,
  });
  await expect(page.locator("#universe-members")).toContainText("601288.SH");
  const customId = new URL(page.url()).searchParams.get("universe_snapshot");
  const csrf = { "X-CSRF-Token": session.csrf_token };
  const invalid = await page.request.post(`${root}/custom`, {
    headers: csrf,
    data: { name: "invalid", instrument_ids: ["999999.SH"] },
  });
  assert.equal(invalid.status(), 400);
  const ambiguous = await (
    await page.request.get(
      `${root}/definitions?search=${encodeURIComponent("恒大概念")}`,
    )
  ).json();
  assert.equal(ambiguous.items[0].source_status, "ambiguous");
  assert.equal(
    (
      await page.request.post(`${root}/resolve`, {
        headers: csrf,
        data: { universe_id: ambiguous.items[0].universe_id },
      })
    ).status(),
    503,
  );
  assert.equal(
    (await page.request.get(`${root}/snapshots/${customId}`)).status(),
    200,
  );
  const other = await page.request.get(
    `${api}/api/v1/workspaces/00000000-0000-4000-8000-000000000001/data/universes/snapshots/${customId}`,
  );
  assert.equal(other.status(), 404);
  writeFileSync(
    "../../.tools/verification-local/review/universes-results.txt",
    "PASS: actual SSO UI -> catalog search -> real MaiRui normalization -> immutable 177-member snapshot -> BJ retained -> desktop/mobile Axe/layout -> refresh/hash -> member paging -> real 300-member index constituent UI/refresh -> custom dedup -> invalid/ambiguous/anonymous/cross-scope rejection. Does not establish batch collection readiness.\n",
  );
  console.log("Actual universe catalog, snapshots and UI: PASS");
} finally {
  await browser.close();
}
