import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

// Prepared via a controlled Worker transaction, not a real supplier or UI
// task-creation fixture. This checks the deployed rejection read/refresh path.
const fixture = JSON.parse(
  readFileSync(
    "../../.tools/verification-local/processing-failure-fixture.json",
  ),
);
const budget = JSON.parse(
  readFileSync("../../.tools/verification-local/budget-scope.json"),
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
  const oldSession = await (
    await page.request.get(`${api}/auth/session`)
  ).json();
  await page
    .getByLabel("切换工作区", { exact: true })
    .selectOption(fixture.scope_id);
  await page.getByRole("button", { name: "切换", exact: true }).click();
  await expect(page).toHaveURL(`${origin}/`, { timeout: 60000 });
  const url = `${origin}/workspace/data?collector_job=${fixture.job_id}`;
  await page.goto(url, { timeout: 90000 });
  const session = await (await page.request.get(`${api}/auth/session`)).json();
  assert.equal(session.workspace_id, fixture.scope_id);
  const root = `${api}/api/v1/workspaces/${fixture.scope_id}/data/collector`;
  const path = `${root}/jobs/${fixture.job_id}/parts/${fixture.part_id}/processing-failures`;
  const response = await page.request.get(path);
  assert.equal(response.status(), 200);
  const records = await response.json();
  assert.equal(records.length, 1);
  assert.equal(records[0].failure_id, fixture.failure_id);
  assert.equal(records[0].normalized_record_count, null);
  assert(
    !JSON.stringify(records).includes("lease_token") &&
      !JSON.stringify(records).includes("body_base64"),
  );
  const job = await (
    await page.request.get(`${root}/jobs/${fixture.job_id}`)
  ).json();
  assert.equal(job.state, "failed");
  assert(job.parts.every((p) => p.publication === null));
  checks.push(
    "scoped persisted rejection; unknown quantity; no publication or private response/lease",
  );
  const run = page.locator(`[data-collector-job="${fixture.job_id}"]`);
  await expect(run).toBeVisible({ timeout: 90000 });
  const button = run
    .getByRole("button", { name: "查看质量报告", exact: true })
    .first();
  await button.focus();
  await page.keyboard.press("Enter");
  await expect(
    run.getByLabel("历史转换阻断记录", { exact: true }),
  ).toContainText("标准记录数量未知", { timeout: 30000 });
  await expect(run).toContainText("collection-normalization-check.v1");
  checks.push(
    "keyboard opens historical normalization check, distinct from quality success",
  );
  for (const viewport of [
    { width: 1440, height: 1000 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(viewport);
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    const axe = await new AxeBuilder({ page })
      .include("#independent-collector")
      .withTags(["wcag2a", "wcag2aa"])
      .analyze();
    assert.deepEqual(
      axe.violations.map((v) => v.id),
      [],
    );
  }
  await run.screenshot({
    path: "../../.tools/verification-local/review/processing-failure-mobile.png",
  });
  checks.push("desktop/mobile no overflow; axe zero violations");
  await page.reload({ timeout: 90000 });
  await expect(run).toBeVisible({ timeout: 90000 });
  await button.click();
  await expect(run).toContainText("标准记录数量未知", { timeout: 30000 });
  assert.deepEqual(await (await page.request.get(path)).json(), records);
  checks.push("refresh retains exact immutable rejection evidence");
  assert.equal(
    (
      await page.request.get(
        `${root}/jobs/${budget.first_job}/parts/${fixture.part_id}/processing-failures`,
      )
    ).status(),
    404,
  );
  assert(
    [403, 404].includes(
      (
        await page.request.get(
          `${api}/api/v1/workspaces/${oldSession.workspace_id}/data/collector/jobs/${fixture.job_id}/parts/${fixture.part_id}/processing-failures`,
        )
      ).status(),
    ),
  );
  const anonymous = await browser.newContext();
  assert.equal((await anonymous.request.get(path)).status(), 401);
  await anonymous.close();
  checks.push("other job/space and anonymous requests rejected");
  writeFileSync(
    "../../.tools/verification-local/review/processing-failure-ui-results.json",
    JSON.stringify(
      {
        checks,
        integration:
          "controlled source/Worker fixture; deployed SSO/UI/API/real DB/archive; not live supplier failure",
      },
      null,
      2,
    ),
  );
  console.log(`PASS ${checks.length} deployed controlled rejection UI checks`);
} finally {
  await browser.close();
}
