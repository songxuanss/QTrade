/** B2 scope UX acceptance on the real deployed stack: global scope bar,
 * inheritance grouping, instrument drawer and the mobile selection bar.
 * UI/navigation only; no supplier readiness is inferred. */
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium, expect as playwrightExpect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const expect = playwrightExpect.configure({ timeout: 30000 });
const root = "../../.tools/";
const admin = JSON.parse(readFileSync(root + "sso-local/state.json"));
const scope = JSON.parse(
  readFileSync(root + "verification-local/budget-scope.json"),
).scope_id;
const origin = process.env.QTRADE_WEB_ORIGIN ?? "http://127.0.0.1:3120";
const review = root + "verification-local/review/";
const report = {
  origin,
  checks: [],
  pools_available: false,
  passed: false,
};
const save = () =>
  writeFileSync(review + "b2-scope-ux.json", JSON.stringify(report, null, 2));
save();
const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
});
const page = await context.newPage();
page.setDefaultTimeout(30000);
let phase = "login";
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));

async function login() {
  await page.goto(origin + "/workspace/data?data_view=download", {
    timeout: 90000,
  });
  await page
    .getByRole("textbox", { name: "邮箱", exact: true })
    .fill("qtrade-integration@example.test");
  await page.getByLabel("密码", { exact: true }).fill(admin.password);
  await page.getByRole("button", { name: "登 录", exact: true }).click();
  await page.waitForURL(
    (u) => ["/workspace/data", "/auth/consent"].includes(u.pathname),
    { timeout: 90000 },
  );
  if (page.url().includes("/auth/consent"))
    await page.getByRole("button", { name: "同意授权", exact: true }).click();
  await page.getByLabel("切换工作区", { exact: true }).selectOption(scope);
  await page.getByRole("button", { name: "切换", exact: true }).click();
  await page.waitForURL((u) => u.pathname === "/", { timeout: 60000 });
}

async function downloadView() {
  await page.goto(origin + "/workspace/data?data_view=download", {
    timeout: 90000,
  });
  await expect(
    page.locator(
      '[data-workbench-tabs="data_view"] [data-workbench-view="download"]',
    ),
  ).toBeVisible();
}

try {
  await login();
  phase = "desktop scope bar";
  await downloadView();
  const bar = page.locator("[data-workbench-scope-bar]");
  await expect(bar).toBeVisible();
  await expect(bar).toContainText("当前采集范围");
  await expect(bar.locator('[data-scope-segment="kind"]')).toContainText(
    "价格行情",
  );
  const panel = page.locator("#independent-collector");
  const instruments = panel.getByLabel("独立采集标的", { exact: true });
  await instruments.fill("");
  const start = panel.getByLabel("独立采集开始日期", { exact: true });
  const end = panel.getByLabel("独立采集结束日期", { exact: true });
  await start.fill("");
  await end.fill("");
  await expect(bar.locator('[data-scope-segment="target"]')).toContainText(
    "尚未选择标的",
  );
  await expect(bar.locator('[data-scope-segment="dates"]')).toContainText(
    "未设置",
  );
  await instruments.fill("601288.SH, 600519.SH");
  await expect(bar.locator('[data-scope-segment="target"]')).toContainText(
    "2 个手工标的",
  );
  await start.fill("2025-01-01");
  await end.fill("2025-12-31");
  await expect(bar.locator('[data-scope-segment="dates"]')).toContainText(
    "2025-01-01 → 2025-12-31",
  );
  await instruments.blur();
  await end.blur();
  report.checks.push({ scope_bar_reflects_draft: true });

  phase = "inheritance grouping";
  const poolBoxes = panel.locator(
    ".collector-pool-options input[type=checkbox]",
  );
  const poolCount = await poolBoxes.count();
  report.pools_available = poolCount > 0;
  if (poolCount > 0) {
    await poolBoxes.first().check();
    await expect(
      bar.locator('[data-scope-inherited="true"]'),
      "the inherited marker must appear once a pool is selected",
    ).toBeVisible();
    await expect(bar.locator('[data-scope-segment="target"]')).toContainText(
      "1 个股票池",
    );
    await expect(instruments).toHaveValue("601288.SH, 600519.SH");
    await panel.getByRole("button", { name: "解除继承", exact: true }).click();
    await expect(bar.locator('[data-scope-inherited="true"]')).toHaveCount(0);
    await expect(instruments).toHaveValue("601288.SH, 600519.SH");
    report.checks.push({ detach_keeps_manual: true });
  } else {
    report.checks.push({ detach_keeps_manual: "skipped: no saved pools" });
  }
  await expect(
    page.locator(
      '[data-workbench-tabs="data_view"] [data-workbench-view="download"]',
    ),
  ).toBeVisible();
  await page.screenshot({
    path: review + "b2-scope-desktop.png",
    fullPage: true,
  });

  phase = "drawer add and remove";
  const trigger = panel.getByRole("button", {
    name: "从清单选择",
    exact: true,
  });
  await trigger.click();
  let dialog = page.locator("[data-instrument-drawer]");
  await expect(dialog).toBeVisible();
  await expect(dialog).toHaveAttribute("aria-modal", "true");
  const search = dialog.getByLabel("搜索标的清单", { exact: true });
  await expect(search).toBeFocused();
  await search.fill("600519");
  const manualRow = dialog.locator('[data-drawer-member="600519.SH"]');
  await expect(manualRow).toBeVisible();
  await manualRow.getByRole("checkbox").uncheck();
  await dialog
    .getByRole("button", { name: "加入本次范围", exact: true })
    .click();
  await expect(dialog).toHaveCount(0);
  await expect(instruments).toHaveValue("601288.SH");
  await expect(trigger).toBeFocused();
  report.checks.push({ drawer_removal_and_focus_return: true });

  if (poolCount > 0) {
    await trigger.click();
    dialog = page.locator("[data-instrument-drawer]");
    const memberBoxes = dialog.locator("[data-drawer-member]");
    const boxCount = await memberBoxes.count();
    let added = 0;
    for (let index = 0; index < boxCount; index += 1) {
      const box = memberBoxes.nth(index).getByRole("checkbox");
      if (!(await box.isDisabled()) && !(await box.isChecked())) {
        await box.check();
        added += 1;
        break;
      }
    }
    await dialog
      .getByRole("button", { name: "加入本次范围", exact: true })
      .click();
    if (added) {
      await expect(instruments).not.toHaveValue("601288.SH");
      report.checks.push({ drawer_add_from_pool: true });
    } else {
      report.checks.push({
        drawer_add_from_pool: "skipped: no selectable member",
      });
    }
  }

  phase = "drawer escape";
  await trigger.click();
  dialog = page.locator("[data-instrument-drawer]");
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
  report.checks.push({ drawer_escape_and_focus: true });

  phase = "scope bar jump";
  await page.getByRole("tab", { name: "任务与结果", exact: true }).click();
  await expect(
    page.locator(
      '[data-workbench-tabs="data_view"] [data-workbench-view="jobs"]',
    ),
  ).toBeVisible();
  await bar.locator('[data-scope-segment="target"]').click();
  await expect(
    page.locator(
      '[data-workbench-tabs="data_view"] [data-workbench-view="download"]',
    ),
  ).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(() => document.activeElement?.getAttribute("aria-label")),
    )
    .toBe("独立采集标的");
  report.checks.push({ scope_bar_jumps_to_field: true });

  phase = "desktop axe";
  const desktopAxe = await new AxeBuilder({ page })
    .include('[data-workbench-tabs="data_view"]')
    .analyze();
  assert.equal(
    desktopAxe.violations.length,
    0,
    JSON.stringify(desktopAxe.violations.map((v) => v.id)),
  );
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    "horizontal overflow at 1440",
  );
  report.checks.push({ desktop_axe: 0 });

  phase = "mobile selection bar";
  await page.setViewportSize({ width: 390, height: 844 });
  await downloadView();
  // A full load starts from the persisted preferences, so enter a draft again
  // before checking the narrow-screen summary.
  await panel
    .getByLabel("独立采集标的", { exact: true })
    .fill("601288.SH, 600519.SH");
  const mobileBar = page.locator("[data-mobile-scope-bar]");
  await expect(mobileBar).toBeVisible();
  await expect(mobileBar).toContainText("已选");
  const barBox = await mobileBar.boundingBox();
  const viewport = page.viewportSize();
  assert(
    barBox && Math.abs(barBox.y + barBox.height - viewport.height) < 4,
    "the mobile bar must sit on the viewport bottom",
  );
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    "horizontal overflow at 390",
  );
  await expect(
    panel.getByRole("button", { name: "预览独立采集计划", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: review + "b2-scope-mobile.png",
    fullPage: true,
  });
  await mobileBar
    .getByRole("button", { name: "查看清单", exact: true })
    .click();
  dialog = page.locator("[data-instrument-drawer]");
  await expect(dialog).toBeVisible();
  const dialogBox = await dialog.boundingBox();
  assert(
    dialogBox && dialogBox.width <= 390.5,
    `drawer width ${dialogBox?.width}`,
  );
  const mobileAxe = await new AxeBuilder({ page })
    .include("[data-instrument-drawer]")
    .analyze();
  assert.equal(
    mobileAxe.violations.length,
    0,
    JSON.stringify(mobileAxe.violations.map((v) => v.id)),
  );
  await page.screenshot({
    path: review + "b2-scope-mobile-drawer.png",
    fullPage: false,
  });
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  report.checks.push({ mobile_bar_and_drawer: true });

  assert.equal(errors.length, 0, errors.join("\n"));
  report.passed = true;
  save();
  console.log(JSON.stringify({ passed: true, checks: report.checks }));
} catch (error) {
  save();
  console.error(
    JSON.stringify({
      passed: false,
      phase,
      failure: `${error.name}: ${String(error.message).split("\n")[0].slice(0, 240)}`,
      page_errors: errors.slice(0, 3),
      url: page.url(),
    }),
  );
  await page
    .screenshot({ path: review + "b2-scope-diagnostic.png", fullPage: true })
    .catch(() => {});
  process.exitCode = 1;
} finally {
  await browser.close();
}
