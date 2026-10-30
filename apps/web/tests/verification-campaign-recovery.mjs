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
  const root = `${api}/api/v1/workspaces/${session.workspace_id}/data`;

  const runs = await (await page.request.get(`${root}/campaigns`)).json();
  const latest = runs.find(
    (r) =>
      r.member_count === 42 &&
      r.planned_parts === 84 &&
      (!process.env.QTRADE_VERIFY_COMPLETED_CAMPAIGN ||
        r.state === "completed"),
  );
  assert.ok(latest);
  const id = latest.campaign_id;
  await page.goto(`${origin}/workspace/data?campaign=${id}`);
  let run = await (await page.request.get(`${root}/campaigns/${id}`)).json();
  const preserved = run.members.flatMap((m) =>
    m.parts.filter((p) => p.state === "viewable").map((p) => p.task_id),
  );
  const didRetry = run.failed_parts > 0;
  if (didRetry) {
    await page
      .getByRole("button", { name: "仅重试失败任务", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "仅重试失败任务", exact: true }),
    ).toHaveCount(0, { timeout: 30000 });
    console.log("Failed-only retry submitted through UI");
  }
  const deadline = Date.now() + 900000;
  while (Date.now() < deadline) {
    run = await (await page.request.get(`${root}/campaigns/${id}`)).json();
    if (run.state === "completed") break;
    assert.equal(
      run.failed_parts,
      0,
      "Live campaign still has provider failures after retry; completion is not established",
    );
    await page.waitForTimeout(3000);
  }
  assert.equal(run.state, "completed");
  assert.equal(run.viewable_members, 42);
  assert.equal(run.failed_parts, 0);
  assert.equal(run.expanded_parts, 84);
  const second = await (
    await page.request.get(`${root}/campaigns/${id}?member_offset=30`)
  ).json();
  assert.equal(second.members.length, 12);
  const parts = [...run.members, ...second.members].flatMap((m) => m.parts);
  assert.equal(new Set(parts.map((p) => p.task_id)).size, 84);
  assert.ok(
    preserved.every((id) =>
      parts.some((p) => p.task_id === id && p.state === "viewable"),
    ),
  );
  assert.ok(parts.every((p) => p.raw_records > 0 && p.state === "viewable"));
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.reload();
    await expect(page.locator(".campaign-progress")).toContainText(
      "可查看 42/42",
      { timeout: 60000 },
    );
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
      false,
    );
    assert.equal(
      (await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze())
        .violations.length,
      0,
    );
    await page
      .locator("#pool-download-title")
      .scrollIntoViewIfNeeded({ timeout: 10000 });
    await page.screenshot({
      path: `../../.tools/verification-local/review/campaign-final-${width}.png`,
      fullPage: false,
    });
  }
  await page
    .locator('[aria-label="下载成员分页"]')
    .getByRole("button", { name: "下一页", exact: true })
    .click();
  await expect(page.locator('[aria-label="下载成员分页"]')).toContainText(
    "第 2 页",
  );
  await page.goto(
    `${origin}/workspace/market?instrument=601288.SH&granularity=1d`,
  );
  await expect(page.locator(".market-chart-legend")).toContainText("根 Bar", {
    timeout: 60000,
  });
  await page.reload();
  await expect(page.locator(".market-chart-legend")).toContainText("根 Bar", {
    timeout: 60000,
  });
  writeFileSync(
    "../../.tools/verification-local/review/campaign-recovery-results.txt",
    `PASS real 42-stock/84-part campaign: UI failed-only retry=${didRetry ? "exercised" : "not exercised (already completed run)"}, nonempty archives/published bars, original successful task identities preserved, desktop/mobile Axe/overflow/refresh, page2, exact daily chart/refresh.\n`,
  );
  console.log("Real full-bank campaign recovery and UI validation: PASS");
} finally {
  await browser.close();
}
