/** Real SSO/API-backed navigation acceptance; no supplier readiness inferred. */
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { openDataView } from "./data-workbench-navigation.mjs";

const root = "../../.tools/verification-local/";
const admin = JSON.parse(readFileSync("../../.tools/sso-local/state.json"));
const reader = JSON.parse(readFileSync(root + "approver.json"));
const scope = JSON.parse(readFileSync(root + "budget-scope.json")).scope_id;
const origin = process.env.QTRADE_WEB_ORIGIN ?? "http://127.0.0.1:13000";
const baseline = process.argv.includes("--baseline");
const mobileOnly = process.argv.includes("--mobile-only");
const suffix = mobileOnly ? "-mobile" : "";
const browser = await chromium.launch();
const checks = [];
let phase = "login";
let activePage;
async function login(page, role) {
  const destination =
    origin +
    "/workspace/data" +
    (baseline ? "" : "?data_view=monitor&monitor_view=sources");
  phase = `${role} login redirect`;
  await page.goto(destination, { timeout: 90000 });
  phase = `${role} login form`;
  await page
    .getByRole("textbox", { name: "邮箱", exact: true })
    .fill(role === "admin" ? "qtrade-integration@example.test" : reader.email);
  await page
    .getByLabel("密码", { exact: true })
    .fill(role === "admin" ? admin.password : reader.password);
  await page.getByRole("button", { name: "登 录", exact: true }).click();
  phase = `${role} oauth return`;
  await page.waitForURL(
    (u) => ["/workspace/data", "/auth/consent"].includes(u.pathname),
    { timeout: 90000 },
  );
  if (page.url().includes("/auth/consent"))
    await page.getByRole("button", { name: "同意授权", exact: true }).click();
  phase = `${role} deep URL`;
  await expect(page).toHaveURL(destination, { timeout: 90000 });
  await expect(
    page.getByRole("heading", { name: "数据管理", exact: true }),
  ).toBeVisible({ timeout: 90000 });
  phase = `${role} deep selected tab`;
  if (!baseline)
    await expect(
      page.getByRole("tab", { name: "来源能力", exact: true }),
    ).toHaveAttribute("aria-selected", "true");
  phase = `${role} workspace selection`;
  if (!(await page.getByLabel("切换工作区", { exact: true }).isVisible()))
    await page.getByRole("button", { name: "打开主导航", exact: true }).click();
  await page.getByLabel("切换工作区", { exact: true }).selectOption(scope);
  await page.getByRole("button", { name: "切换", exact: true }).click();
  await expect(page).toHaveURL(origin + "/", { timeout: 60000 });
  await page.goto(origin + "/workspace/data");
}
async function dimensions(page) {
  return page.evaluate(() => ({
    height: document.querySelector(".data-workbench").getBoundingClientRect()
      .height,
    width: innerWidth,
    overflow: document.documentElement.scrollWidth > innerWidth,
  }));
}
try {
  for (const role of baseline || mobileOnly ? ["admin"] : ["admin", "reader"]) {
    const context = await browser.newContext({
      viewport: {
        width: role === "admin" && !mobileOnly ? 1440 : 390,
        height: role === "admin" && !mobileOnly ? 1000 : 844,
      },
    });
    const page = await context.newPage();
    activePage = page;
    page.setDefaultTimeout(30000);
    const errors = [];
    page.on("pageerror", () => errors.push("browser_error"));
    phase = role + " login";
    await login(page, role);
    if (baseline) {
      for (const width of [1440, 390]) {
        await page.setViewportSize({
          width,
          height: width === 1440 ? 1000 : 844,
        });
        checks.push(await dimensions(page));
      }
      await context.close();
      continue;
    }
    const main = page.locator('[data-workbench-tabs="data_view"]');
    const active = main.locator(":scope > [data-workbench-view]:visible");
    await expect(active).toHaveCount(1);
    await expect(active).toHaveAttribute("data-workbench-view", "download");
    const first = await dimensions(page);
    assert(!first.overflow);
    assert(
      first.height < 3000,
      "default workspace grew beyond the compact layout bound",
    );
    if (role === "admin")
      await page.getByLabel("独立采集标的", { exact: true }).fill("601288.SH");
    for (const [title, key] of [
      ["数据目录", "catalog"],
      ["多维下载", "download"],
      ["任务与结果", "jobs"],
      ["来源与新鲜度", "sources"],
    ]) {
      phase = role + " " + key;
      await openDataView(page, title);
      await expect(active).toHaveCount(1);
      await expect(active).toHaveAttribute("data-workbench-view", key);
      assert(!(await dimensions(page)).overflow);
      const axe = await new AxeBuilder({ page })
        .include('[data-workbench-tabs="data_view"]')
        .analyze();
      assert.equal(axe.violations.length, 0);
    }
    for (const secondary of [
      "研究数据集",
      "原始批次",
      "质量问题",
      "兼容采集任务",
    ]) {
      phase = role + " assets " + secondary;
      await openDataView(page, "任务与结果", secondary);
      assert.equal(
        (
          await new AxeBuilder({ page })
            .include('[data-workbench-tabs="data_view"]')
            .analyze()
        ).violations.length,
        0,
      );
      assert(!(await dimensions(page)).overflow);
    }
    phase = role + " monitor tools";
    await openDataView(page, "来源与新鲜度", "来源能力");
    await page
      .locator("summary")
      .filter({ hasText: /^来源能力与证据$/ })
      .click();
    // The catalog grows as supplier feeds connect (11 profiles at the first
    // snapshot, 12 after the factor and rights feeds). Cross-check the
    // rendered count against the deployed catalog instead of freezing a
    // stale literal; the floor keeps a collapsed catalog loud.
    const catalogResponse = await page.request.get(
      `http://127.0.0.1:3122/api/v1/workspaces/${scope}/data/collector/source-profiles`,
    );
    assert.equal(catalogResponse.status(), 200);
    const profileCount = (await catalogResponse.json()).profiles.length;
    assert(
      profileCount >= 12,
      `source profile catalog shrank: ${profileCount}`,
    );
    await expect(
      page.getByText(`已读取 ${profileCount} 项来源能力说明`, { exact: true }),
    ).toBeVisible({ timeout: 60000 });
    await expect(
      page.getByRole("region", { name: "来源证据明细", exact: true }),
    ).toBeVisible();
    assert.equal(
      (
        await new AxeBuilder({ page })
          .include('[data-workbench-tabs="data_view"]')
          .analyze()
      ).violations.length,
      0,
    );
    await openDataView(page, "来源与新鲜度", "数据新鲜度");
    await page
      .locator("summary")
      .filter({ hasText: /^新鲜度健康$/ })
      .click();
    await expect(page.getByText(/健康快照已更新 · 第 1 页/)).toBeVisible({
      timeout: 60000,
    });
    const region = page.getByRole("region", {
      name: "新鲜度记录",
      exact: true,
    });
    await expect(region).toBeVisible();
    phase = role + " health viewport size";
    const viewportHeight = await region.evaluate((node) => node.clientHeight);
    assert(viewportHeight <= 621, `health viewport height ${viewportHeight}`);
    await region.focus();
    await page.keyboard.press("PageDown");
    phase = role + " health expanded accessibility";
    const expandedAxe = await new AxeBuilder({ page })
      .include('[data-workbench-tabs="data_view"]')
      .analyze();
    if (expandedAxe.violations.length)
      console.error(
        JSON.stringify(
          expandedAxe.violations.map((v) => ({
            id: v.id,
            targets: v.nodes.map((n) => n.target),
          })),
        ),
      );
    assert.equal(expandedAxe.violations.length, 0);
    await openDataView(page, "来源与新鲜度", "用量与配额");
    await page.getByRole("button", { name: "刷新用量", exact: true }).click();
    await expect(page.getByText("用量已更新", { exact: true })).toBeVisible();
    await openDataView(page, "多维下载");
    if (role === "admin")
      await expect(
        page.getByLabel("独立采集标的", { exact: true }),
      ).toHaveValue("601288.SH");
    // Automatic activation uses roving focus and visible native controls.
    const firstTab = page
      .getByRole("tablist", { name: "数据管理分区", exact: true })
      .getByRole("tab", { name: "数据目录", exact: true });
    await firstTab.focus();
    await page.keyboard.press("ArrowRight");
    await expect(active).toHaveAttribute("data-workbench-view", "download");
    await page.keyboard.press("End");
    await expect(active).toHaveAttribute("data-workbench-view", "sources");
    await page.keyboard.press("Home");
    await expect(active).toHaveAttribute("data-workbench-view", "catalog");
    await openDataView(page, "多维下载", "分红、送转与配股");
    await page.reload();
    await expect(page.locator("#distribution-collection")).toBeVisible();
    await expect(page.locator("#financial-collection")).not.toBeVisible();
    await page.getByRole("button", { name: "刷新数据", exact: true }).click();
    await expect(page.locator("#distribution-collection")).toBeVisible();
    await openDataView(page, "来源与新鲜度", "来源能力");
    await page.goBack();
    await expect(
      page.getByRole("tab", { name: "用量与配额", exact: true }),
    ).toHaveAttribute("aria-selected", "true");
    await page.goBack();
    await expect(page.locator("#distribution-collection")).toBeVisible();
    await page.goForward();
    await expect(active).toHaveAttribute("data-workbench-view", "sources");
    await page.goto(origin + "/workspace/data#financial-collection");
    await expect(page.locator("#financial-collection")).toBeVisible();
    if (role === "reader")
      await expect(
        page.getByRole("button", { name: "预览财务计划", exact: true }),
      ).toBeDisabled();
    await page.goto(origin + "/workspace/data?universe_search=");
    await expect(active).toHaveAttribute("data-workbench-view", "catalog");
    await page.goto(origin + "/workspace/data?data_view=unknown");
    await expect(active).toHaveAttribute("data-workbench-view", "download");
    phase = role + " legacy result navigation";
    await page.goto(origin + "/workspace/data?result=research_saved");
    await expect(active).toHaveAttribute("data-workbench-view", "jobs");
    await expect(page.locator("#research-data")).toBeVisible();
    if (role === "admin") {
      const taskEndpoint = `http://127.0.0.1:3122/api/v1/workspaces/${scope}/data/tasks`;
      const beforeResponse = await page.request.get(taskEndpoint);
      assert.equal(beforeResponse.status(), 200);
      const before = await beforeResponse.json();
      await openDataView(page, "多维下载", "逐笔与兼容");
      const form = page.locator("#legacy-downloads");
      await form
        .getByLabel("A 股代码（6 位）", { exact: true })
        .fill("601288.SH");
      await form
        .getByLabel("开始时间（UTC）", { exact: true })
        .fill("2026-10-16T00:00");
      await form
        .getByLabel("结束时间（UTC）", { exact: true })
        .fill("2026-10-15T00:00");
      phase = role + " refresh preserves keyboard-edited drafts";
      const refreshed = page.waitForResponse(
        (response) =>
          response.request().method() === "GET" &&
          response.request().headers().rsc === "1" &&
          new URL(response.url()).pathname === "/workspace/data",
      );
      await page.getByRole("button", { name: "刷新数据", exact: true }).focus();
      await page.keyboard.press("Enter");
      await refreshed;
      await expect(
        form.getByLabel("A 股代码（6 位）", { exact: true }),
      ).toHaveValue("601288.SH");
      await expect(
        form.getByLabel("开始时间（UTC）", { exact: true }),
      ).toHaveValue("2026-10-16T00:00");
      await expect(
        form.getByLabel("结束时间（UTC）", { exact: true }),
      ).toHaveValue("2026-10-15T00:00");
      await form
        .getByRole("button", { name: "创建 Tick 下载任务", exact: true })
        .focus();
      await page.keyboard.press("Enter");
      await expect(page).toHaveURL(
        (u) =>
          u.searchParams.get("data_view") === "download" &&
          u.searchParams.get("research_view") === "ticks" &&
          u.searchParams.get("result") === "invalid",
      );
      await expect(active).toHaveAttribute("data-workbench-view", "download");
      phase = role + " legacy rejection business alert";
      await expect(
        page.locator(".data-workbench").getByRole("alert"),
      ).toContainText("下载参数不符合要求");
      phase = role + " legacy rejection restores only safe inputs";
      await expect(
        form.getByLabel("A 股代码（6 位）", { exact: true }),
      ).toHaveValue("601288.SH");
      await expect(
        form.getByLabel("开始时间（UTC）", { exact: true }),
      ).toHaveValue("2026-10-16T00:00");
      await expect(
        form.getByLabel("结束时间（UTC）", { exact: true }),
      ).toHaveValue("2026-10-15T00:00");
      assert.equal(
        new URL(page.url()).searchParams.has("authorization_id"),
        false,
      );
      assert.equal(page.url().includes("mairui-default"), false);
      phase = role + " legacy rejection unchanged persistence";
      const afterResponse = await page.request.get(taskEndpoint);
      assert.equal(afterResponse.status(), 200);
      assert.deepEqual(await afterResponse.json(), before);
    }
    await page.goto(
      origin + "/workspace/data?data_view=monitor&monitor_view=sources",
    );
    await expect(active).toHaveAttribute("data-workbench-view", "sources");
    await expect
      .poll(() =>
        page
          .getByRole("tablist", { name: "数据管理分区", exact: true })
          .evaluate((list) => {
            const selected = list
              .querySelector('[aria-selected="true"]')
              .getBoundingClientRect();
            const frame = list.getBoundingClientRect();
            return (
              selected.left >= frame.left - 1 &&
              selected.right <= frame.right + 1
            );
          }),
      )
      .toBe(true);
    await page
      .locator("summary")
      .filter({ hasText: /^来源能力与证据$/ })
      .click();
    await expect(
      page.getByText("已读取 11 项来源能力说明", { exact: true }),
    ).toBeVisible({ timeout: 60000 });
    await page.screenshot({
      path: root + `review/data-layout-${role}${suffix}-monitor.png`,
      fullPage: true,
    });
    await openDataView(page, "多维下载");
    const final = await dimensions(page);
    assert(!final.overflow);
    assert.equal(errors.length, 0);
    await page.screenshot({
      path: root + `review/data-layout-${role}${suffix}-collection.png`,
      fullPage: true,
    });
    checks.push({
      role,
      default: first,
      final,
      browser_errors: errors.length,
      four_views: true,
      axe_violations: 0,
      draft_retained_on_switch: role === "admin",
      navigation_persistence: true,
      monitor_live_read: true,
      deep_login_restored: true,
    });
    await context.close();
  }
  writeFileSync(
    root +
      `review/data-layout-${baseline ? "baseline" : "results"}${suffix}.json`,
    JSON.stringify(
      {
        scope_id: scope,
        checks,
        integration: "real SSO/deployed API/persistence; navigation only",
      },
      null,
      2,
    ),
    { mode: 0o600 },
  );
  console.log(
    `PASS data workbench ${baseline ? "baseline captured" : "navigation, roles, keyboard, persistence, axe0 and no overflow"}`,
  );
} catch (error) {
  console.error(`FAIL data layout at ${phase}: ${error?.name ?? "Error"}`);
  if (activePage) {
    const url = new URL(activePage.url());
    if (url.pathname === "/workspace/data")
      await activePage.screenshot({
        path: root + "review/data-layout-diagnostic.png",
        fullPage: true,
      });
    console.error(
      JSON.stringify({
        pathname: url.pathname,
        view:
          url.pathname === "/workspace/data"
            ? url.searchParams.get("data_view")
            : null,
        visible_tabs: await activePage
          .getByRole("tab", { selected: true })
          .allTextContents(),
      }),
    );
  }
  process.exitCode = 1;
} finally {
  await browser.close();
}
