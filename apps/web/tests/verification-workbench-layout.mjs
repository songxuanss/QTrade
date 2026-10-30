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
const origin = "http://127.0.0.1:13000";
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
    await expect(active).toHaveAttribute("data-workbench-view", "collect");
    const first = await dimensions(page);
    assert(!first.overflow);
    assert(
      first.height < 3000,
      "default workspace grew beyond the compact layout bound",
    );
    if (role === "admin")
      await page.getByLabel("独立采集标的", { exact: true }).fill("601288.SH");
    for (const [title, key] of [
      ["行情与因子", "collect"],
      ["板块与股票池", "pools"],
      ["财务与事件", "fundamentals"],
      ["自动更新", "schedules"],
      ["数据资产", "assets"],
      ["运行监控", "monitor"],
      ["逐笔与兼容", "ticks"],
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
      await openDataView(page, "数据资产", secondary);
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
    await openDataView(page, "运行监控", "来源能力");
    await page
      .locator("summary")
      .filter({ hasText: /^来源能力与证据$/ })
      .click();
    await expect(
      page.getByText("已读取 11 项来源能力说明", { exact: true }),
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
    await openDataView(page, "运行监控", "数据新鲜度");
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
    await openDataView(page, "运行监控", "用量与配额");
    await page.getByRole("button", { name: "刷新用量", exact: true }).click();
    await expect(page.getByText("用量已更新", { exact: true })).toBeVisible();
    await openDataView(page, "行情与因子");
    if (role === "admin")
      await expect(
        page.getByLabel("独立采集标的", { exact: true }),
      ).toHaveValue("601288.SH");
    // Automatic activation uses roving focus and visible native controls.
    const firstTab = page
      .getByRole("tablist", { name: "数据管理分区", exact: true })
      .getByRole("tab", { name: "行情与因子", exact: true });
    await firstTab.focus();
    await page.keyboard.press("ArrowRight");
    await expect(active).toHaveAttribute("data-workbench-view", "pools");
    await page.keyboard.press("End");
    await expect(active).toHaveAttribute("data-workbench-view", "ticks");
    await page.keyboard.press("Home");
    await expect(active).toHaveAttribute("data-workbench-view", "collect");
    await openDataView(page, "财务与事件", "分红与送转");
    await page.reload();
    await expect(page.locator("#distribution-collection")).toBeVisible();
    await expect(page.locator("#financial-collection")).not.toBeVisible();
    await page.getByRole("button", { name: "刷新数据", exact: true }).click();
    await expect(page.locator("#distribution-collection")).toBeVisible();
    await openDataView(page, "运行监控", "来源能力");
    await page.goBack();
    await expect(
      page.getByRole("tab", { name: "用量与配额", exact: true }),
    ).toHaveAttribute("aria-selected", "true");
    await page.goBack();
    await expect(page.locator("#distribution-collection")).toBeVisible();
    await page.goForward();
    await expect(active).toHaveAttribute("data-workbench-view", "monitor");
    await page.goto(origin + "/workspace/data#financial-collection");
    await expect(page.locator("#financial-collection")).toBeVisible();
    if (role === "reader")
      await expect(
        page.getByRole("button", { name: "预览财务计划", exact: true }),
      ).toBeDisabled();
    await page.goto(origin + "/workspace/data?universe_search=");
    await expect(active).toHaveAttribute("data-workbench-view", "pools");
    await page.goto(origin + "/workspace/data?data_view=unknown");
    await expect(active).toHaveAttribute("data-workbench-view", "collect");
    phase = role + " legacy result navigation";
    await page.goto(origin + "/workspace/data?result=research_saved");
    await expect(active).toHaveAttribute("data-workbench-view", "assets");
    await expect(page.locator("#research-data")).toBeVisible();
    if (role === "admin") {
      const taskEndpoint = `http://127.0.0.1:3122/api/v1/workspaces/${scope}/data/tasks`;
      const beforeResponse = await page.request.get(taskEndpoint);
      assert.equal(beforeResponse.status(), 200);
      const before = await beforeResponse.json();
      await openDataView(page, "逐笔与兼容");
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
      await form
        .getByRole("button", { name: "创建 Tick 下载任务", exact: true })
        .click();
      await expect(page).toHaveURL(
        (u) =>
          u.searchParams.get("data_view") === "ticks" &&
          u.searchParams.get("result") === "invalid",
      );
      await expect(active).toHaveAttribute("data-workbench-view", "ticks");
      phase = role + " legacy rejection business alert";
      await expect(
        page.locator(".data-workbench").getByRole("alert"),
      ).toContainText("下载参数不符合要求");
      phase = role + " legacy rejection unchanged persistence";
      const afterResponse = await page.request.get(taskEndpoint);
      assert.equal(afterResponse.status(), 200);
      assert.deepEqual(await afterResponse.json(), before);
    }
    await page.goto(
      origin + "/workspace/data?data_view=monitor&monitor_view=sources",
    );
    await expect(active).toHaveAttribute("data-workbench-view", "monitor");
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
    await openDataView(page, "行情与因子");
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
      seven_views: true,
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
