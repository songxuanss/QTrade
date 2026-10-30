/** Capture a real browser session, then prove it and frozen data survive recovery.
 * Storage state stays in the ignored private directory, never in test output. */
import assert from "node:assert/strict";
import { readFileSync, writeFileSync, chmodSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
const read = (p) => JSON.parse(readFileSync(`../../.tools/${p}`));
const admin = read("sso-local/state.json");
const reader = read("verification-local/approver.json");
const scope = read("verification-local/budget-scope.json").scope_id;
const job = read("verification-local/review/layout-density-after.json").job_id;
const origin = "http://127.0.0.1:13000";
const folder = "../../.tools/verification-local/review/";
const storage = folder + "runtime-recovery-browser-private.json";
const action = process.argv[2];
assert(["before", "after"].includes(action));
const browser = await chromium.launch();
let phase = "login";
async function login(page, role) {
  await page.goto(origin + "/workspace/data", { timeout: 90000 });
  await page
    .getByRole("textbox", { name: "邮箱", exact: true })
    .fill(role === "admin" ? "qtrade-integration@example.test" : reader.email);
  await page
    .getByLabel("密码", { exact: true })
    .fill(role === "admin" ? admin.password : reader.password);
  await page.getByRole("button", { name: "登 录", exact: true }).click();
  await page.waitForURL(
    (u) => ["/workspace/data", "/auth/consent"].includes(u.pathname),
    { timeout: 90000 },
  );
  if (new URL(page.url()).pathname === "/auth/consent")
    await page.getByRole("button", { name: "同意授权", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "数据管理", exact: true }),
  ).toBeVisible({ timeout: 90000 });
  if (!(await page.getByLabel("切换工作区", { exact: true }).isVisible()))
    await page.getByRole("button", { name: "打开主导航", exact: true }).click();
  await page.getByLabel("切换工作区", { exact: true }).selectOption(scope);
  await page.getByRole("button", { name: "切换", exact: true }).click();
  await expect(page).toHaveURL(origin + "/", { timeout: 60000 });
  await page.goto(origin + "/workspace/data", { timeout: 90000 });
}
try {
  const context = await browser.newContext(
    action === "after" ? { storageState: storage } : {},
  );
  const page = await context.newPage();
  page.setDefaultTimeout(30000);
  let errors = 0;
  page.on("pageerror", () => errors++);
  if (action === "before") await login(page, "admin");
  else {
    phase = "original session retained";
    const response = await page.request.get(
      "http://127.0.0.1:3122/auth/session",
    );
    assert.equal(response.status(), 200);
    assert.equal((await response.json()).workspace_id, scope);
  }
  const checks = [];
  for (const width of [1440, 390]) {
    phase = `${width} original published data`;
    await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 });
    await page.goto(
      `${origin}/workspace/data?data_view=collect&collector_job=${job}`,
      { timeout: 90000 },
    );
    const panel = page.locator("#independent-collector");
    await expect(panel.locator("[data-collector-job]")).toHaveAttribute(
      "data-collector-job",
      job,
      { timeout: 90000 },
    );
    await panel
      .getByRole("button", { name: "查看行情记录", exact: true })
      .first()
      .click();
    await expect(
      panel.getByLabel("采集数据预览", { exact: true }).locator("tbody tr"),
    ).toHaveCount(5);
    await page.reload({ timeout: 90000 });
    await expect(panel.locator("[data-collector-job]")).toHaveAttribute(
      "data-collector-job",
      job,
      { timeout: 90000 },
    );
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    const axe = await new AxeBuilder({ page })
      .include("#independent-collector")
      .analyze();
    assert.equal(axe.violations.length, 0);
    await page.mouse.click(5, 5);
    await page.screenshot({
      path: folder + `runtime-recovery-${action}-${width}.png`,
      fullPage: true,
    });
    checks.push({
      width,
      existing_task: true,
      records: 5,
      refresh: true,
      axe: 0,
    });
  }
  if (action === "before") {
    writeFileSync(storage, JSON.stringify(await context.storageState()), {
      mode: 0o600,
    });
    chmodSync(storage, 0o600);
  }
  assert.equal(errors, 0);
  writeFileSync(
    folder + `runtime-recovery-browser-${action}.json`,
    JSON.stringify({ passed: true, checks }),
    { mode: 0o600 },
  );
  console.log(JSON.stringify({ passed: true, action, checks }));
  await context.close();
} catch (error) {
  console.error(
    JSON.stringify({
      passed: false,
      phase,
      assertion:
        error instanceof assert.AssertionError ? error.message : error.name,
    }),
  );
  process.exitCode = 1;
} finally {
  await browser.close();
}
