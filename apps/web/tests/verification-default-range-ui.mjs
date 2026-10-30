import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
const identity = JSON.parse(readFileSync("../../.tools/sso-local/state.json"));
const browser = await chromium.launch();
const checks = [];
try {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto("http://127.0.0.1:13000/workspace/data", { timeout: 90000 });
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
  const panel = page.locator("#independent-collector");
  const button = panel.getByRole("button", {
    name: "使用最近自然年日期",
    exact: true,
  });
  await expect(button).toBeEnabled({ timeout: 90000 });
  await button.click();
  const first = panel.getByLabel("独立采集开始日期", { exact: true });
  const last = panel.getByLabel("独立采集结束日期", { exact: true });
  await expect(first).not.toHaveValue("", { timeout: 90000 });
  const range = {
    first: await first.inputValue(),
    last: await last.inputValue(),
  };
  assert(range.first < range.last);
  const session = await (
    await page.request.get("http://127.0.0.1:3122/auth/session")
  ).json();
  const endpoint = `http://127.0.0.1:3122/api/v1/workspaces/${session.workspace_id}/data/collector/default-range`;
  const response = await page.request.get(endpoint, {
    params: { source: "mairui.history.bars", granularity: "1d" },
  });
  assert.equal(response.status(), 200);
  const contract = await response.json();
  assert.equal(contract.first, range.first);
  assert.equal(contract.last, range.last);
  assert.equal(contract.endpoint_semantics, "start_inclusive_end_exclusive");
  assert.equal(contract.availability, "estimated_not_verified");
  const unsupported = await page.request.get(endpoint, {
    params: { source: "mairui.factor.history", granularity: "1d" },
  });
  assert.equal(unsupported.status(), 400);
  assert.equal((await unsupported.json()).detail.code, "unsupported");
  await expect(
    panel.getByRole("status").filter({ hasText: "开始包含、结束不包含" }),
  ).toBeVisible();
  checks.push({
    name: "real_supplier_calendar_default_ui",
    range,
    passed: true,
  });
  if (process.env.QTRADE_VERIFY_ANNUAL === "true") {
    await first.fill("");
    await last.fill("");
    await panel.getByLabel("独立采集标的", { exact: true }).fill("601288.SH");
    await expect(
      panel.getByLabel("独立采集下载方式", { exact: true }),
    ).toHaveValue("missing");
    await panel
      .getByLabel("独立采集下载方式", { exact: true })
      .selectOption("all");
    await panel
      .getByRole("button", { name: "预览独立采集计划", exact: true })
      .click();
    await expect(panel.getByLabel("独立采集计划", { exact: true })).toBeVisible(
      { timeout: 90000 },
    );
    await expect(first).toHaveValue(range.first);
    await expect(last).toHaveValue(range.last);
    await panel
      .getByRole("button", { name: "提交独立采集任务", exact: true })
      .click();
    await expect(panel.locator("[data-collector-job]")).toBeVisible({
      timeout: 90000,
    });
    const id = await panel
      .locator("[data-collector-job]")
      .getAttribute("data-collector-job");
    let job;
    const deadline = Date.now() + 300000;
    while (Date.now() < deadline) {
      const result = await page.request.get(
        endpoint.replace("default-range", `jobs/${id}`),
      );
      assert.equal(result.status(), 200);
      job = await result.json();
      assert.notEqual(job.state, "failed", "annual live download failed");
      if (
        job.state === "completed" &&
        job.parts.every((part) => part.consumer?.state === "visible")
      )
        break;
      await page.waitForTimeout(1500);
    }
    assert.equal(job.state, "completed");
    assert.equal(job.request.granularity, "1d");
    assert.deepEqual(job.request.instrument_ids, ["601288.SH"]);
    assert(
      job.parts.every(
        (part) =>
          part.publication?.record_count > 0 &&
          part.publication?.raw_record_count > 0 &&
          part.consumer?.state === "visible",
      ),
    );
    const records = job.parts.reduce(
      (sum, part) => sum + part.publication.record_count,
      0,
    );
    assert(records > 200, "annual sample unexpectedly sparse");
    await page.reload({ timeout: 90000 });
    await expect(panel.locator(`[data-collector-job="${id}"]`)).toBeVisible({
      timeout: 90000,
    });
    await expect(panel).toContainText("农业银行");
    await panel
      .getByRole("link", { name: "终端可见 · 查看图表", exact: true })
      .first()
      .click();
    await expect(page.locator("#market-chart-heading")).toContainText(
      "农业银行",
      { timeout: 30000 },
    );
    await expect(page.locator(".market-chart svg")).toBeVisible();
    await page.reload({ timeout: 90000 });
    await expect(page.locator("#market-chart-heading")).toContainText(
      "农业银行",
    );
    await expect(
      page
        .getByLabel("图表周期")
        .getByRole("button", { name: "1d", exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
    const chartResponse = await page.request.get(
      endpoint.replace("data/collector/default-range", "market-watch/chart") +
        `?instrument_id=601288.SH&granularity=1d&days=30&dataset_id=${job.parts[0].consumer.dataset_id}`,
    );
    assert.equal(chartResponse.status(), 200);
    const chart = await chartResponse.json();
    assert(chart.bars.length > 0);
    const published = new Set(
      job.parts.map((part) => part.publication.publication_id),
    );
    assert(chart.bars.every((bar) => published.has(bar.dataset_id)));
    checks.push({
      name: "annual_daily_ui_worker_publication_consumer_refresh",
      id,
      records,
      publications: job.parts.map((part) => part.publication.publication_id),
      chart_records: chart.bars.length,
      chart_dataset_ids: [...new Set(chart.bars.map((bar) => bar.dataset_id))],
      passed: true,
    });
    await page.goto("http://127.0.0.1:13000/workspace/data", {
      timeout: 90000,
    });
  }
  await first.fill("2025-09-01");
  await last.fill("2025-09-05");
  await expect(button).toBeDisabled();
  await expect(first).toHaveValue("2025-09-01");
  await expect(last).toHaveValue("2025-09-05");
  checks.push({ name: "explicit_dates_not_replaced", passed: true });
  const pool = panel.getByLabel("独立采集股票池", { exact: true });
  await pool.selectOption({ index: 1 });
  const poolId = await pool.inputValue();
  await panel.getByLabel("独立采集标的", { exact: true }).fill("601288.SH");
  await last.fill("");
  await panel
    .getByRole("button", { name: "预览独立采集计划", exact: true })
    .click();
  await expect(panel).toContainText("请同时填写开始和结束日期");
  await expect(first).toHaveValue("2025-09-01");
  await expect(last).toHaveValue("");
  await expect(panel.getByLabel("独立采集计划", { exact: true })).toHaveCount(
    0,
  );
  checks.push({
    name: "partial_date_rejected_without_silent_default",
    passed: true,
  });
  await expect(panel.getByLabel("独立采集标的", { exact: true })).toHaveValue(
    "601288.SH",
  );
  await expect(pool).toHaveValue(poolId);
  await first.fill("");
  await panel
    .getByLabel("采集数据类别", { exact: true })
    .selectOption("factors");
  await panel
    .getByRole("button", { name: "预览独立采集计划", exact: true })
    .click();
  await expect(panel).toContainText("默认发布时间尚未验证");
  await expect(first).toHaveValue("");
  await expect(last).toHaveValue("");
  checks.push({
    name: "unknown_factor_release_not_replaced_by_daily_rule",
    passed: true,
  });
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 950 });
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    const axe = await new AxeBuilder({ page })
      .include("#independent-collector")
      .analyze();
    assert.equal(axe.violations.length, 0);
    checks.push({ name: `layout_accessibility_${width}`, passed: true });
  }
  writeFileSync(
    `../../.tools/verification-local/review/${process.env.QTRADE_VERIFY_ANNUAL === "true" ? "annual-daily" : "default-range"}-ui-results.json`,
    JSON.stringify(
      {
        checks,
        limitation:
          process.env.QTRADE_VERIFY_ANNUAL === "true"
            ? "Annual mode verifies v1 live collection; comprehensive publication gates and scheduling remain separate acceptance"
            : "Input/default/rejection verification only; not annual download acceptance",
      },
      null,
      2,
    ),
  );
  console.log(JSON.stringify({ checks }));
} finally {
  await browser.close();
}
