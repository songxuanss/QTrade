import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

// Controlled executor/raw rejection + terminal SQL fixture, not UI creation
// or a live vendor failure. Deployed read/refresh/permission path is real.
const fixtures = JSON.parse(
  readFileSync(
    "../../.tools/verification-local/research-processing-fixtures.json",
  ),
);
const identity = JSON.parse(readFileSync("../../.tools/sso-local/state.json"));
const origin = "http://127.0.0.1:13000",
  api = "http://127.0.0.1:3122";
const browser = await chromium.launch();
const checks = [];
try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  const page = await context.newPage();
  await page.goto(`${origin}/workspace/data`, { timeout: 90000 });
  await page
    .getByRole("textbox", { name: "邮箱", exact: true })
    .fill("qtrade-integration@example.test");
  await page.getByLabel("密码", { exact: true }).fill(identity.password);
  await page.getByRole("button", { name: "登 录", exact: true }).click();
  await page.waitForURL(
    (u) => u.pathname === "/workspace/data" || u.pathname === "/auth/consent",
    { timeout: 90000 },
  );
  if (page.url().includes("/auth/consent"))
    await page.getByRole("button", { name: "同意授权", exact: true }).click();
  await page.waitForURL((u) => u.pathname === "/workspace/data", {
    timeout: 90000,
  });
  const before = await (await page.request.get(`${api}/auth/session`)).json();
  await page
    .getByLabel("切换工作区", { exact: true })
    .selectOption(fixtures[0].scope_id);
  await page.getByRole("button", { name: "切换", exact: true }).click();
  await expect(page).toHaveURL(`${origin}/`, { timeout: 60000 });
  await page.goto(`${origin}/workspace/data`, { timeout: 90000 });
  const session = await (await page.request.get(`${api}/auth/session`)).json();
  assert.equal(session.workspace_id, fixtures[0].scope_id);
  for (const fixture of fixtures) {
    const root = `${api}/api/v1/workspaces/${fixture.scope_id}/data/collector/research`;
    const path = `${root}/jobs/${fixture.request_id}/processing-failures`;
    const response = await page.request.get(path);
    assert.equal(response.status(), 200);
    const records = await response.json();
    assert.equal(records.length, 1);
    assert.equal(records[0].failure_id, fixture.failure_id);
    assert.equal(records[0].normalized_record_count, null);
    assert.equal(records[0].batch_id, fixture.batch_id);
    assert.equal(records[0].kind, fixture.kind);
    assert(
      !JSON.stringify(records).includes("bad-number") &&
        !JSON.stringify(records).includes("lease_id"),
    );
    const job = await (
      await page.request.get(`${root}/jobs/${fixture.request_id}`)
    ).json();
    assert.equal(job.state, "failed");
    assert.equal(job.publication_id, null);
    const name = fixture.kind === "financial_statement" ? "财务" : "公司行为";
    const history = page.getByRole("region", {
      name: `${name}任务列表`,
      exact: true,
    });
    const search = page.getByRole("searchbox", {
      name: `搜索${name}近期任务`,
      exact: true,
    });
    const loaded = await history.locator("tbody tr").count();
    assert(loaded > 0);
    await search.fill("no-matching-task-fixture");
    await expect(history.locator("tbody tr")).toHaveCount(0);
    await expect(history).toContainText("没有匹配的近期任务");
    await search.fill(fixture.request_id);
    await expect(history.locator("tbody tr")).toHaveCount(1);
    await expect(history.locator("tbody tr")).toHaveAttribute(
      "data-research-job",
      fixture.request_id,
    );
    await search.fill("");
    await expect(history.locator("tbody tr")).toHaveCount(loaded);
    assert(await history.evaluate((el) => el.clientHeight <= 440));
    await history.focus();
    await page.keyboard.press("PageDown");
    await search.fill(fixture.request_id);
    const row = page.locator(`[data-research-job="${fixture.request_id}"]`);
    await expect(row).toBeVisible({ timeout: 90000 });
    const button = row.getByRole("button", {
      name: "查看转换检查",
      exact: true,
    });
    await button.focus();
    await page.keyboard.press("Enter");
    await expect(
      row.getByLabel("研究转换阻断记录", { exact: true }),
    ).toContainText("标准记录数量未知", { timeout: 30000 });
    await expect(row).toContainText("research-normalization-check.v1");
    await page.setViewportSize({ width: 390, height: 844 });
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    const panel =
      fixture.kind === "financial_statement"
        ? "#financial-collection"
        : "#distribution-collection";
    assert(await history.evaluate((el) => el.clientHeight <= 360));
    const axe = await new AxeBuilder({ page })
      .include(panel)
      .withTags(["wcag2a", "wcag2aa"])
      .analyze();
    assert.deepEqual(
      axe.violations.map((v) => v.id),
      [],
    );
    // Inspect the actual mobile viewport with a normal field focused. A tall
    // element screenshot can relocate fixed keyboard-navigation overlays.
    await search.focus();
    await page.screenshot({
      path: `../../.tools/verification-local/review/research-history-${fixture.kind}-mobile.png`,
      fullPage: false,
    });
    await page.reload({ timeout: 90000 });
    await expect(row).toBeVisible({ timeout: 90000 });
    await button.click();
    await expect(row).toContainText("标准记录数量未知", { timeout: 30000 });
    assert.deepEqual(await (await page.request.get(path)).json(), records);
    assert(
      [403, 404].includes(
        (
          await page.request.get(
            `${api}/api/v1/workspaces/${before.workspace_id}/data/collector/research/jobs/${fixture.request_id}/processing-failures`,
          )
        ).status(),
      ),
    );
    const anonymous = await browser.newContext();
    assert.equal((await anonymous.request.get(path)).status(), 401);
    await anonymous.close();
    checks.push(
      `${fixture.kind}: searchable bounded history / no-match and clear / exact task action / scoped immutable check / no publication / keyboard / mobile axe0 / refresh / foreign space and anonymous reject`,
    );
    await page.setViewportSize({ width: 1440, height: 1000 });
  }
  writeFileSync(
    "../../.tools/verification-local/review/research-processing-ui-results.json",
    JSON.stringify(
      {
        checks,
        integration:
          "controlled executor/terminal SQL fixtures; deployed UI/API/real DB/raw archive; no live supplier failure claim",
      },
      null,
      2,
    ),
  );
  console.log("PASS 2 research category rejection UI flows");
} finally {
  await browser.close();
}
