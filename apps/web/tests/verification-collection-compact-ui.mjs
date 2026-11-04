/** Deployed UI -> API -> Worker -> live source -> publication -> refreshed UI.
 * Private auth remains in memory; reports contain only task IDs and counts. */
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { openDataView } from "./data-workbench-navigation.mjs";
const read = (p) => JSON.parse(readFileSync(`../../.tools/${p}`));
const admin = read("sso-local/state.json");
const reader = read("verification-local/approver.json");
const scope = read("verification-local/budget-scope.json").scope_id;
const namespace =
  process.env.QTRADE_COLLECTION_REPORT ?? "collection-compact-ui";
assert(/^[a-z0-9-]+$/.test(namespace));
const path = `../../.tools/verification-local/review/${namespace}.json`;
const origin = process.env.QTRADE_WEB_ORIGIN ?? "http://127.0.0.1:13000";
const endpoint = `http://127.0.0.1:3122/api/v1/workspaces/${scope}/data/collector`;
const resume = process.argv.includes("--resume");
const report = resume ? JSON.parse(readFileSync(path)) : { checks: [] };
report.passed = false;
const save = () => writeFileSync(path, JSON.stringify(report, null, 2));
save();
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
  for (const role of ["admin", "reader"]) {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
    });
    const page = await context.newPage();
    page.setDefaultTimeout(30000);
    let browserErrors = 0;
    page.on("pageerror", () => browserErrors++);
    await login(page, role);
    const panel = page.locator("#independent-collector");
    const results = page.locator("#collector-results");
    if (role === "admin" && !resume) {
      phase = "draft retention";
      await panel
        .getByLabel("采集数据类别", { exact: true })
        .selectOption("bars");
      await panel
        .getByLabel("采集数据来源", { exact: true })
        .selectOption("mairui.history.bars");
      await panel
        .getByLabel("独立采集周期", { exact: true })
        .selectOption("1d");
      await panel.getByLabel("独立采集标的", { exact: true }).fill("601288.SH");
      await panel
        .getByLabel("独立采集开始日期", { exact: true })
        .fill("2025-09-01");
      await panel
        .getByLabel("独立采集结束日期", { exact: true })
        .fill("2025-09-05");
      await panel
        .getByLabel("独立采集下载方式", { exact: true })
        .selectOption("all");
      await openDataView(page, "任务与结果");
      await expect(
        panel.getByRole("button", { name: "预览独立采集计划", exact: true }),
      ).not.toBeVisible();
      await openDataView(page, "多维下载");
      await expect(
        panel.getByLabel("独立采集标的", { exact: true }),
      ).toHaveValue("601288.SH");
      await expect(
        panel.getByLabel("独立采集开始日期", { exact: true }),
      ).toHaveValue("2025-09-01");
      phase = "incomplete date rejection";
      await panel.getByLabel("独立采集结束日期", { exact: true }).fill("");
      await panel
        .getByRole("button", { name: "预览独立采集计划", exact: true })
        .click();
      await expect(panel.getByRole("alert")).toContainText(
        "请同时填写开始和结束日期",
      );
      await panel
        .getByLabel("独立采集结束日期", { exact: true })
        .fill("2025-09-05");
      report.checks.push({ incomplete_dates_rejected: true });
      phase = "preview and submit";
      await panel
        .getByRole("button", { name: "预览独立采集计划", exact: true })
        .click();
      await expect(
        panel.getByLabel("独立采集计划", { exact: true }),
      ).toBeVisible({ timeout: 90000 });
      const ack = panel.getByRole("checkbox", {
        name: "确认计划调用规模",
        exact: true,
      });
      if (await ack.count()) await ack.check();
      await panel
        .getByRole("button", { name: "提交独立采集任务", exact: true })
        .click();
      const run = results.locator("[data-collector-job]");
      await expect(run).toBeVisible({ timeout: 90000 });
      report.job_id = await run.getAttribute("data-collector-job");
      save();
      await expect(
        page
          .getByRole("tablist", { name: "数据管理分区", exact: true })
          .getByRole("tab", { name: "任务与结果", exact: true }),
      ).toHaveAttribute("aria-selected", "true");
      phase = "live worker and consumer";
      let job;
      await expect
        .poll(
          async () => {
            const response = await page.request.get(
              `${endpoint}/jobs/${report.job_id}`,
            );
            assert.equal(response.status(), 200);
            job = await response.json();
            return (
              job.state === "completed" &&
              job.parts.every((p) => p.consumer?.state === "visible")
            );
          },
          { timeout: 180000, intervals: [1000, 2000] },
        )
        .toBe(true);
      assert.equal(
        job.parts.reduce((n, p) => n + p.publication.record_count, 0),
        5,
      );
      assert(job.parts.every((p) => p.publication.raw_record_count > 0));
      if (namespace === "bar-flags-download") {
        for (const part of job.parts) {
          assert.equal(
            part.publication.mapping_version,
            "mairui.history-bars.v3",
          );
          const rows = await (
            await page.request.get(
              `${endpoint}/publications/${part.publication.publication_id}/records`,
            )
          ).json();
          assert.equal(rows.records.length, 5);
          assert(rows.records.every((r) => r.quality === "valid"));
          const checks = await (
            await page.request.get(
              `${endpoint}/jobs/${job.job_id}/parts/${part.part_id}/quality`,
            )
          ).json();
          assert(checks.some((q) => q.status === "passed"));
        }
        report.numeric_zero_verified = true;
      }
      report.parts = job.parts.map((p) => ({
        part_id: p.part_id,
        publication_id: p.publication.publication_id,
        records: p.publication.record_count,
      }));
      report.live_completed = true;
      save();
    }
    assert(report.job_id);
    for (const width of [1440, 390]) {
      phase = `${role} ${width} results`;
      await page.setViewportSize({
        width,
        height: width === 1440 ? 1000 : 844,
      });
      if (role === "admin") {
        phase = `${role} ${width} create layout`;
        await page.goto(
          origin + "/workspace/data?data_view=collect&collect_view=create",
          { timeout: 90000 },
        );
        await expect(
          panel.getByRole("button", { name: "预览独立采集计划", exact: true }),
        ).toBeVisible();
        const createHeight = await panel.evaluate(
          (n) => n.getBoundingClientRect().height,
        );
        const createAxe = await new AxeBuilder({ page })
          .include("#independent-collector")
          .analyze();
        assert.equal(
          createAxe.violations.length,
          0,
          JSON.stringify(createAxe.violations.map((v) => v.id)),
        );
        assert(
          await page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth,
          ),
        );
        // Native disclosure must stay keyboard reachable; opening it must not
        // reset the selected instrument or invalidate the prepared date input.
        const help = panel.getByText("来源能力与采集说明", { exact: true });
        if (await help.count()) {
          const input = panel.getByLabel("独立采集标的", { exact: true });
          const before = await input.inputValue();
          await help.focus();
          await page.keyboard.press("Enter");
          await expect(help.locator("..")).toHaveAttribute("open", "");
          await expect(input).toHaveValue(before);
          await page.keyboard.press("Enter");
        }
        await page.mouse.click(5, 5);
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.screenshot({
          path: `../../.tools/verification-local/review/${namespace}-create-${width}.png`,
          fullPage: true,
        });
        report.checks.push({
          role,
          width,
          create_height: createHeight,
          create_axe: 0,
        });
        save();
        await panel
          .getByLabel("采集数据类别", { exact: true })
          .selectOption("factors");
        await expect(
          panel.getByRole("group", { name: "选择历史因子", exact: true }),
        ).toBeVisible();
        assert(
          await page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth,
          ),
        );
        const factorAxe = await new AxeBuilder({ page })
          .include("#independent-collector")
          .analyze();
        assert.equal(
          factorAxe.violations.length,
          0,
          JSON.stringify(factorAxe.violations.map((v) => v.id)),
        );
        report.checks.push({
          role,
          width,
          factor_form_axe: 0,
          factor_form_no_overflow: true,
        });
        save();
      }
      await page.goto(
        origin +
          `/workspace/data?data_view=collect&collector_job=${report.job_id}`,
        { timeout: 90000 },
      );
      await expect(results.locator("[data-collector-job]")).toHaveAttribute(
        "data-collector-job",
        report.job_id,
        { timeout: 90000 },
      );
      await expect(
        panel.getByLabel("独立采集标的", { exact: true }),
      ).not.toBeVisible();
      await expect(
        results.getByLabel("查看已保存采集任务", { exact: true }),
      ).toHaveValue(report.job_id);
      const parts = results.getByRole("region", {
        name: "采集任务分段",
        exact: true,
      });
      await parts.focus();
      await page.keyboard.press("PageDown");
      assert(await parts.evaluate((n) => n.clientHeight <= 561));
      await results
        .getByRole("button", { name: "查看行情记录", exact: true })
        .first()
        .click();
      await expect(
        results.getByLabel("采集数据预览", { exact: true }).locator("tbody tr"),
      ).toHaveCount(5);
      const height = await results.evaluate(
        (n) => n.getBoundingClientRect().height,
      );
      assert(height < 2000, `result workspace too tall: ${height}`);
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      );
      const axe = await new AxeBuilder({ page })
        .include("#collector-results")
        .analyze();
      assert.equal(
        axe.violations.length,
        0,
        JSON.stringify(axe.violations.map((v) => v.id)),
      );
      await page.screenshot({
        path: `../../.tools/verification-local/review/${namespace}-${role}-${width}.png`,
        fullPage: true,
      });
      report.checks.push({ role, width, result_height: height, axe: 0 });
      save();
      await page.reload({ timeout: 90000 });
      await expect(results.locator("[data-collector-job]")).toHaveAttribute(
        "data-collector-job",
        report.job_id,
        { timeout: 90000 },
      );
      if (role === "admin") {
        await openDataView(page, "多维下载");
        await expect(
          panel.getByLabel("独立采集标的", { exact: true }),
        ).toBeVisible();
        await expect(results.locator("[data-collector-job]")).not.toBeVisible();
        await page.reload({ timeout: 90000 });
        await expect(
          panel.getByLabel("独立采集标的", { exact: true }),
        ).toBeVisible();
      } else {
        await expect(
          panel.getByLabel("独立采集标的", { exact: true }),
        ).toHaveCount(0);
        const session = await (
          await page.request.get("http://127.0.0.1:3122/auth/session")
        ).json();
        const denied = await page.request.post(`${endpoint}/preview`, {
          headers: { "X-CSRF-Token": session.csrf_token },
          data: {
            source: "mairui.history.bars",
            dataset_kind: "bars",
            instrument_ids: ["601288.SH"],
            granularity: "1d",
            start: "2025-09-01T00:00:00Z",
            end: "2025-09-02T00:00:00Z",
            mode: "all",
          },
        });
        assert.equal(denied.status(), 403);
      }
    }
    phase = role + " long persisted result";
    const annual = read(
      "verification-local/review/normalization-input-ui.json",
    ).runs.find((r) => r.kind === "bars");
    await page.goto(
      origin +
        `/workspace/data?data_view=collect&collector_job=${annual.job_id}`,
      { timeout: 90000 },
    );
    await expect(results.locator("[data-collector-job]")).toHaveAttribute(
      "data-collector-job",
      annual.job_id,
      { timeout: 90000 },
    );
    phase = role + " saved task selector";
    await panel
      .getByLabel("查看已保存采集任务", { exact: true })
      .selectOption(report.job_id);
    await expect(results.locator("[data-collector-job]")).toHaveAttribute(
      "data-collector-job",
      report.job_id,
      { timeout: 90000 },
    );
    await page.goto(
      origin +
        `/workspace/data?data_view=collect&collector_job=${annual.job_id}`,
      { timeout: 90000 },
    );
    await expect(results.locator("[data-collector-job]")).toHaveAttribute(
      "data-collector-job",
      annual.job_id,
      { timeout: 90000 },
    );
    phase = role + " annual records";
    await results
      .getByRole("button", { name: "查看行情记录", exact: true })
      .first()
      .click();
    const table = results.getByLabel("采集记录表格", { exact: true });
    await expect(table.locator("tbody tr")).toHaveCount(100);
    const bounds = await table.evaluate((n) => ({
      height: n.clientHeight,
      total: n.scrollHeight,
    }));
    phase = role + " long result scroll bounds";
    assert(bounds.height <= 400 && bounds.total > bounds.height);
    await table.focus();
    await page.keyboard.press("PageDown");
    await expect
      .poll(() => table.evaluate((n) => n.scrollTop))
      .toBeGreaterThan(0);
    phase = role + " second record page";
    const firstRecord = await table.locator("tbody tr").first().innerText();
    await results
      .getByRole("button", { name: "下一页记录", exact: true })
      .click();
    await expect
      .poll(() => table.locator("tbody tr").first().innerText())
      .not.toBe(firstRecord);
    await expect(table.locator("tbody tr")).toHaveCount(100);
    await results
      .getByRole("button", { name: "下一页记录", exact: true })
      .click();
    await expect(table.locator("tbody tr")).toHaveCount(43);
    report.checks.push({
      role,
      long_records: 243,
      pagination: [100, 100, 43],
      bounded_scroll: true,
    });
    save();
    phase = role + " refreshed market chart";
    await page.goto(
      origin +
        `/workspace/data?data_view=collect&collector_job=${report.job_id}`,
      { timeout: 90000 },
    );
    await expect(results.locator("[data-collector-job]")).toHaveAttribute(
      "data-collector-job",
      report.job_id,
      { timeout: 90000 },
    );
    const current = await (
      await page.request.get(`${endpoint}/jobs/${report.job_id}`)
    ).json();
    const dataset = current.parts[0].consumer.dataset_id;
    await results
      .getByRole("link", { name: "终端可见 · 查看图表", exact: true })
      .first()
      .click();
    await expect(page.locator("#market-chart-heading")).toContainText(
      "农业银行",
      { timeout: 30000 },
    );
    assert.equal(new URL(page.url()).searchParams.get("dataset_id"), dataset);
    await page.reload({ timeout: 90000 });
    await expect(page.locator(".market-chart svg")).toBeVisible();
    const chart = await page.request.get(
      `http://127.0.0.1:3122/api/v1/workspaces/${scope}/market-watch/chart?instrument_id=601288.SH&granularity=1d&days=30&dataset_id=${dataset}`,
    );
    assert.equal(chart.status(), 200);
    const bars = (await chart.json()).bars;
    assert.equal(bars.length, 5);
    assert(bars.every((r) => r.dataset_id === dataset));
    report.checks.push({ role, frozen_chart: true, records: 5 });
    save();
    assert.equal(browserErrors, 0);
    await context.close();
  }
  report.passed = true;
  save();
  console.log(
    JSON.stringify({
      passed: true,
      live_completed: report.live_completed,
      checks: report.checks,
    }),
  );
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
