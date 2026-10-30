import { openDataView } from "./data-workbench-navigation.mjs";
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { trackResearchDownload } from "./research-flow-evidence.mjs";

const origin = "http://127.0.0.1:13000";
const confirmSmall = process.argv.includes("--confirm-small");
const identity = JSON.parse(readFileSync("../../.tools/sso-local/state.json"));
const browser = await chromium.launch();
let phase = "login";
let activePage;
try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  const page = await context.newPage();
  activePage = page;
  page.setDefaultTimeout(30000);
  await page.goto(origin + "/workspace/data", { timeout: 90000 });
  await page
    .getByRole("textbox", { name: "邮箱", exact: true })
    .fill("qtrade-integration@example.test");
  await page.getByLabel("密码", { exact: true }).fill(identity.password);
  await page.getByRole("button", { name: "登 录", exact: true }).click();
  await page.waitForURL(
    (url) =>
      url.pathname === "/workspace/data" || url.pathname === "/auth/consent",
    { timeout: 90000 },
  );
  if (page.url().includes("/auth/consent"))
    await page.getByRole("button", { name: "同意授权", exact: true }).click();
  phase = "workspace selection";
  if (process.env.QTRADE_RESEARCH_SCOPE) {
    await expect(page).toHaveURL(origin + "/workspace/data", {
      timeout: 90000,
    });
    await page
      .getByLabel("切换工作区", { exact: true })
      .selectOption(process.env.QTRADE_RESEARCH_SCOPE);
    await page.getByRole("button", { name: "切换", exact: true }).click();
    await expect(page).toHaveURL(origin + "/", { timeout: 60000 });
    await page.goto(origin + "/workspace/data", { timeout: 90000 });
  }
  await openDataView(page, "财务与事件", "财务报表");
  phase = "financial form";
  const panel = page.locator("#financial-collection");
  await expect(panel).toBeVisible({ timeout: 90000 });
  phase = "download observer";
  const finishDownload = await trackResearchDownload(
    page,
    "mairui.financial.income",
    6,
    process.env.QTRADE_RESEARCH_SCOPE,
  );
  await panel.getByLabel("财务来源").selectOption("mairui.financial.income");
  await panel.getByLabel("财务证券代码").fill("600519.SH");
  phase = "large preview";
  // Preview only: trigger default confirmation with a long report-period
  // range, without asking the supplier for that unverified historical span.
  await panel.getByLabel("报告期开始（含）").fill("1900-01-01");
  await panel.getByLabel("报告期结束（不含）").fill("2026-01-01");
  await panel
    .getByRole("button", { name: "预览财务计划", exact: true })
    .click();
  const acknowledgement = panel.getByRole("checkbox", {
    name: "财务确认调用规模",
    exact: true,
  });
  const execute = panel.getByRole("button", {
    name: "执行财务采集",
    exact: true,
  });
  await expect(acknowledgement).not.toBeChecked({ timeout: 30000 });
  await expect(execute).toBeDisabled();
  await acknowledgement.focus();
  await page.keyboard.press("Space");
  await expect(execute).toBeEnabled();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(panel.getByLabel("财务调用估计", { exact: true })).toContainText(
    "不是供应商报价",
  );
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  assert.equal(
    (await new AxeBuilder({ page }).include("#financial-collection").analyze())
      .violations.length,
    0,
  );
  await panel
    .getByRole("heading", { name: "财务采集计划", exact: true })
    .locator("..")
    .screenshot({
      path: "../../.tools/verification-local/review/research-plan-confirmation-mobile.png",
    });
  if (process.argv.includes("--preview-only")) {
    console.log(
      "PASS research plan mobile visual: preview only; no task submitted",
    );
    await browser.close();
    process.exit(0);
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  const session = await (
    await page.request.get("http://127.0.0.1:3122/auth/session")
  ).json();
  const root = `http://127.0.0.1:3122/api/v1/workspaces/${process.env.QTRADE_RESEARCH_SCOPE ?? session.workspace_id}/data/collector/research`;
  const headers = { "X-CSRF-Token": session.csrf_token };
  const previewResponse = await page.request.post(`${root}/preview`, {
    headers,
    data: {
      source: "mairui.financial.income",
      kind: "financial_statement",
      instrument_ids: ["600519.SH"],
      date_axis: "report_period",
      start: "1900-01-01",
      end: "2026-01-01",
      mode: "all",
    },
  });
  assert.equal(previewResponse.status(), 200);
  const large = await previewResponse.json();
  assert(large.confirmation_required);
  const draft = { ...large.request, control_sha256: large.control_sha256 };
  assert.equal(
    (
      await page.request.post(`${root}/jobs`, { headers, data: draft })
    ).status(),
    400,
  );
  for (const confirmed of [1, "true"]) {
    assert.equal(
      (
        await page.request.post(`${root}/jobs`, {
          headers,
          data: { ...draft, confirmed },
        })
      ).status(),
      422,
    );
  }
  assert.equal(
    (
      await page.request.get(`${root}/jobs/${large.request.request_id}`)
    ).status(),
    404,
  );
  await panel.getByLabel("报告期开始（含）").fill("2015-01-01");
  await expect(acknowledgement).toHaveCount(0);
  await panel.getByLabel("报告期结束（不含）").fill("2016-01-01");
  await panel
    .getByRole("button", { name: "预览财务计划", exact: true })
    .click();
  await expect(
    panel.getByRole("heading", { name: "财务采集计划", exact: true }),
  ).toBeVisible({ timeout: 30000 });
  await expect(panel.locator(".research-plan-preview")).toContainText(
    "1 个标的",
  );
  if (confirmSmall) {
    await expect(acknowledgement).not.toBeChecked();
    await expect(execute).toBeDisabled();
    await acknowledgement.focus();
    await page.keyboard.press("Space");
    await expect(execute).toBeEnabled();
  } else await expect(acknowledgement).toHaveCount(0);
  await expect(panel.getByLabel("财务调用估计", { exact: true })).toContainText(
    "0–1",
  );
  await panel
    .getByRole("button", { name: "执行财务采集", exact: true })
    .click();
  const download = await finishDownload();
  const row = panel.locator(`[data-research-job="${download.request_id}"]`);
  for (let i = 0; i < 40; i++) {
    await panel
      .getByRole("button", { name: "刷新财务任务", exact: true })
      .click();
    if ((await row.textContent())?.includes("已发布")) break;
    await page.waitForTimeout(1000);
  }
  await expect(row).toContainText("已发布");
  await row.getByRole("button", { name: "浏览财务版本", exact: true }).click();
  await expect(
    panel.getByRole("heading", { name: "已发布财务版本", exact: true }),
  ).toBeVisible({ timeout: 30000 });
  assert.equal(
    await panel.locator("[data-research-versions] > details").count(),
    6,
  );
  await expect(panel.locator("[data-research-versions]")).toContainText(
    download.content_version,
  );
  await page.reload({ timeout: 90000 });
  await expect(row).toContainText("已发布");
  await row.getByRole("button", { name: "浏览财务版本", exact: true }).click();
  await expect(panel.locator("[data-research-versions] > details")).toHaveCount(
    6,
    { timeout: 30000 },
  );
  const results = [];
  for (const viewport of [
    { width: 1440, height: 1000 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(viewport);
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    );
    const axe = await new AxeBuilder({ page })
      .include("#financial-collection")
      .analyze();
    assert.equal(
      axe.violations.length,
      0,
      JSON.stringify(axe.violations.map((v) => v.id)),
    );
    results.push({ viewport, records: 6, axeViolations: 0 });
  }
  assert.deepEqual(await finishDownload(), download);
  writeFileSync(
    `../../.tools/verification-local/review/financial-ui-results${confirmSmall ? "-confirmed" : ""}.json`,
    JSON.stringify(
      {
        origin,
        confirmed_small: confirmSmall,
        download,
        plan_checks: [
          "large preview only; explicit keyboard acknowledgement",
          "mobile estimate/checkbox axe zero; no overflow",
          "input edit invalidates confirmation",
          "deployed API rejects missing/string/numeric acknowledgement without task",
          confirmSmall
            ? "small fresh plan requires keyboard acknowledgement and completes"
            : "small fresh plan needs no acknowledgement",
        ],
        evidence:
          "live UI/SSO/API/Worker/MaiRui publication and refreshed six revisions",
        results,
      },
      null,
      2,
    ),
  );
  console.log(
    "PASS financial UI: live six revisions, refresh, desktop/mobile axe",
  );
} catch (error) {
  // Original browser errors may contain OAuth callback parameters.
  console.error("FAIL financial UI at " + phase + ":", error?.name ?? "Error");
  if (activePage && !activePage.isClosed()) {
    const panel = activePage.locator("#financial-collection");
    if (await panel.isVisible().catch(() => false)) {
      writeFileSync(
        "../../.tools/verification-local/review/financial-ui-failure.json",
        JSON.stringify({
          phase,
          text: (await panel.innerText()).slice(0, 1400),
        }),
        { mode: 0o600 },
      );
    }
  }
  process.exitCode = 1;
} finally {
  await browser.close();
}
