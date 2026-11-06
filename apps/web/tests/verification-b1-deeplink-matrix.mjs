/** B1 four-view IA deep-link matrix on the real deployed stack.
 * URL -> view resolution only; no supplier readiness inferred. */
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium, expect as playwrightExpect } from "@playwright/test";

// Web-first assertions default to a 5s expect timeout, independent of
// page.setDefaultTimeout. The 3120 verification stack serves the app through
// next dev, so an on-demand compile can exceed 5s after a cold full load.
const expect = playwrightExpect.configure({ timeout: 30000 });

const root = "../../.tools/";
const admin = JSON.parse(readFileSync(root + "sso-local/state.json"));
const reader = JSON.parse(
  readFileSync(root + "verification-local/approver.json"),
);
const scope = JSON.parse(
  readFileSync(root + "verification-local/budget-scope.json"),
).scope_id;
const origin = process.env.QTRADE_WEB_ORIGIN ?? "http://127.0.0.1:3120";
const review = root + "verification-local/review/";
const path = review + "b1-deeplink-matrix.json";
const report = { origin, checks: [], passed: false };
const save = () => writeFileSync(path, JSON.stringify(report, null, 2));
save();
const browser = await chromium.launch();
let phase = "login";
let currentPage;
const pageErrors = [];

async function login(page, role, destination = "/workspace/data") {
  await page.goto(origin + destination, { timeout: 90000 });
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
  // SSO must round-trip the full returnTo, legacy query included.
  await expect(page).toHaveURL(origin + destination, { timeout: 90000 });
  await expect(
    page.getByRole("heading", { name: "数据管理", exact: true }),
  ).toBeVisible({ timeout: 90000 });
  if (
    !(await page
      .getByLabel("切换工作区", { exact: true })
      .isVisible()
      .catch(() => false))
  )
    await page.getByRole("button", { name: "打开主导航", exact: true }).click();
  await page.getByLabel("切换工作区", { exact: true }).selectOption(scope);
  await page.getByRole("button", { name: "切换", exact: true }).click();
  await expect(page).toHaveURL(origin + "/", { timeout: 60000 });
  await page.goto(origin + destination, { timeout: 90000 });
}

const activeView = (page) =>
  page
    .locator('[data-workbench-tabs="data_view"]')
    .locator(":scope > [data-workbench-view]:visible");

/** Deep links arrive as full document loads (bookmarks, SSO returnTo). A
 * query-only goto stays inside the current document and can land before the
 * app's navigation listeners attach, which makes the view resolution flaky.
 * about:blank guarantees a cross-document load while keeping the URL exact. A
 * client-side route change during hydration can still abort the document
 * navigation after many sequential loads, so retry that transport error once;
 * the retry is the same full load a person gets when reloading a bookmark. */
async function deepLink(page, suffix) {
  const target = origin + "/workspace/data" + suffix;
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    await page.goto("about:blank");
    try {
      await page.goto(target, { timeout: 90000 });
      return;
    } catch (error) {
      const aborted = /ERR_ABORTED|ERR_FAILED/.test(String(error?.message));
      if (attempt === 2 || !aborted) throw error;
    }
  }
}

async function expectView(page, key) {
  const active = activeView(page);
  await expect(active).toHaveCount(1);
  await expect(active).toHaveAttribute("data-workbench-view", key);
}

async function expectNested(page, list, tab) {
  await expect(
    page
      .getByRole("tablist", { name: list, exact: true })
      .getByRole("tab", { name: tab, exact: true }),
  ).toHaveAttribute("aria-selected", "true");
}

const job = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";

try {
  // ---- admin desktop: legacy value / anchor / parameter matrix ----
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  const page = await context.newPage();
  currentPage = page;
  page.setDefaultTimeout(30000);
  const errors = [];
  page.on("pageerror", (e) => {
    errors.push(String(e));
    pageErrors.push(String(e));
  });
  await login(page, "admin");
  phase = "admin default";
  await expectView(page, "download");

  const matrix = [
    [`?data_view=collect`, "download"],
    [`?data_view=pools`, "catalog"],
    [`?data_view=fundamentals`, "download"],
    [`?data_view=ticks`, "download"],
    [`?data_view=schedules`, "sources"],
    [`?data_view=monitor`, "sources"],
    [`?data_view=assets`, "jobs"],
    [`?data_view=collect&collect_view=results`, "jobs"],
    [`?data_view=collect&collect_view=create`, "download"],
    [`?data_view=collect&collector_job=${job}`, "jobs"],
    [`?collector_job=${job}`, "jobs"],
    [`?data_view=catalog`, "catalog"],
    [`?data_view=download`, "download"],
    [`?data_view=jobs`, "jobs"],
    [`?data_view=sources`, "sources"],
    [`?data_view=bogus`, "download"],
    ["#independent-collector", "download"],
    ["#financial-collection", "download"],
    ["#distribution-collection", "download"],
    ["#legacy-downloads", "download"],
    ["#pool-download", "catalog"],
    ["#universe-catalog", "catalog"],
    ["#universe-members", "catalog"],
    ["#collection", "jobs"],
    ["#batches", "jobs"],
    ["#quality", "jobs"],
    ["#research-data", "jobs"],
    ["#composition", "jobs"],
    ["#bogus-anchor", "download"],
    [`?universe_search=`, "catalog"],
    [`?universe_category=bank`, "catalog"],
    [`?universe_result=saved`, "catalog"],
    [`?quality_dataset=whatever`, "jobs"],
    [`?result=research_saved`, "jobs"],
    [`?result=composition_saved`, "jobs"],
    [`?composed_dataset=x`, "jobs"],
    [`?compose_snapshot=y`, "jobs"],
    [`?download_instrument=601288.SH`, "download"],
    [`?data_view=monitor&monitor_view=usage`, "sources"],
    [`?data_view=assets&asset_view=batches`, "jobs"],
    [`?data_view=collect&research_view=ticks`, "download"],
  ];
  for (const [suffix, key] of matrix) {
    phase = `admin matrix ${suffix}`;
    await deepLink(page, suffix);
    await expectView(page, key);
  }
  report.checks.push({ admin_matrix: matrix.length });

  phase = "admin nested defaults";
  await deepLink(page, "?data_view=ticks");
  await expectNested(page, "其他下载入口", "逐笔与兼容");
  await deepLink(page, "?data_view=schedules");
  await expectNested(page, "来源与运行监控", "自动更新");
  await deepLink(page, "?quality_dataset=whatever");
  await expectNested(page, "数据资产类别", "质量问题");
  await deepLink(page, "?download_instrument=601288.SH");
  await expectNested(page, "其他下载入口", "逐笔与兼容");
  report.checks.push({ nested_defaults: 4 });

  phase = "admin draft survives view switches";
  await deepLink(page, "");
  const panel = page.locator("#independent-collector");
  await panel.getByLabel("独立采集标的", { exact: true }).fill("601288.SH");
  await panel
    .getByLabel("独立采集开始日期", { exact: true })
    .fill("2025-09-01");
  const tabs = page.getByRole("tablist", { name: "数据管理分区", exact: true });
  await tabs.getByRole("tab", { name: "任务与结果", exact: true }).click();
  await expectView(page, "jobs");
  await tabs.getByRole("tab", { name: "来源与新鲜度", exact: true }).click();
  await expectView(page, "sources");
  await tabs.getByRole("tab", { name: "多维下载", exact: true }).click();
  await expectView(page, "download");
  await expect(panel.getByLabel("独立采集标的", { exact: true })).toHaveValue(
    "601288.SH",
  );
  await expect(
    panel.getByLabel("独立采集开始日期", { exact: true }),
  ).toHaveValue("2025-09-01");
  report.checks.push({ draft_across_views: true });
  await page.screenshot({
    path: review + "b1-matrix-admin-download.png",
    fullPage: true,
  });

  phase = "admin mobile four views";
  await page.setViewportSize({ width: 390, height: 844 });
  for (const title of ["数据目录", "多维下载", "任务与结果", "来源与新鲜度"]) {
    await tabs.getByRole("tab", { name: title, exact: true }).click();
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      `horizontal overflow in ${title} at 390px`,
    );
  }
  await tabs.getByRole("tab", { name: "多维下载", exact: true }).click();
  await expectView(page, "download");
  await page.screenshot({
    path: review + "b1-matrix-mobile-download.png",
    fullPage: true,
  });
  report.checks.push({ mobile_no_overflow: true });
  assert.equal(errors.length, 0, errors.join("\n"));
  await context.close();

  // ---- reader: default jobs, explicit params respected, no create form ----
  const readerContext = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  const readerPage = await readerContext.newPage();
  currentPage = readerPage;
  readerPage.setDefaultTimeout(30000);
  const readerErrors = [];
  readerPage.on("pageerror", (e) => {
    readerErrors.push(String(e));
    pageErrors.push(String(e));
  });
  await login(readerPage, "reader");
  phase = "reader default";
  await expectView(readerPage, "jobs");
  await expect(
    readerPage.locator("#independent-collector").getByLabel("独立采集标的", {
      exact: true,
    }),
  ).toHaveCount(0);
  await readerPage.screenshot({
    path: review + "b1-matrix-reader-jobs.png",
    fullPage: true,
  });

  phase = "reader explicit download";
  await deepLink(readerPage, "?data_view=download");
  await expectView(readerPage, "download");
  await expect(
    readerPage.locator("#independent-collector").getByLabel("独立采集标的", {
      exact: true,
    }),
  ).toHaveCount(0);

  phase = "reader legacy collect_view=create";
  await deepLink(readerPage, "?data_view=collect&collect_view=create");
  await expectView(readerPage, "download");

  phase = "reader sso returnTo legacy assets";
  await readerContext.clearCookies();
  await login(readerPage, "reader", `/workspace/data?data_view=assets`);
  await expectView(readerPage, "jobs");
  report.checks.push({ reader_default_jobs: true, reader_explicit: 3 });
  assert.equal(readerErrors.length, 0, readerErrors.join("\n"));
  await readerContext.close();

  report.passed = true;
  save();
  console.log(JSON.stringify({ passed: true, checks: report.checks }));
} catch (error) {
  save();
  let dump = {};
  if (currentPage) {
    try {
      await currentPage.screenshot({
        path: review + "b1-matrix-diagnostic.png",
        fullPage: true,
      });
      dump = {
        url: currentPage.url(),
        body: (
          await currentPage
            .evaluate(() => document.body.innerText)
            .catch(() => "")
        )
          .replace(/\s+/g, " ")
          .slice(0, 300),
      };
    } catch {
      dump = { url: "unavailable" };
    }
  }
  console.error(
    JSON.stringify({
      passed: false,
      phase,
      assertion:
        error instanceof assert.AssertionError
          ? error.message
          : `${error.name}: ${String(error.message).split("\n")[0].slice(0, 200)}`,
      page_errors: pageErrors.slice(0, 3),
      ...dump,
    }),
  );
  process.exitCode = 1;
} finally {
  await browser.close();
}
