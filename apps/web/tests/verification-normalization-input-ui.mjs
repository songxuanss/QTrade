/** Actual download regression for the prepared conversion seam, not replay UI. */
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
const read = (p) => JSON.parse(readFileSync(`../../.tools/${p}`));
const readOnly = process.argv.includes("--read-only");
const resume = process.argv.includes("--resume");
const readerMode = process.argv.includes("--reader");
assert(!readerMode || readOnly);
const admin = read("sso-local/state.json");
const reader = read("verification-local/approver.json");
const scope = read("verification-local/budget-scope.json").scope_id;
const origin = "http://127.0.0.1:13000";
const root = `http://127.0.0.1:3122/api/v1/workspaces/${scope}/data/collector`;
const report =
  readOnly || resume
    ? {
        ...read("verification-local/review/normalization-input-ui.json"),
        read_only: true,
        role: readerMode ? "reader" : "admin",
      }
    : {
        scope_id: scope,
        runs: [],
        integration:
          "real SSO/UI/API/Postgres/Worker/MaiRui/RAW/quality/publication/consumer",
      };
const file = `../../.tools/verification-local/review/normalization-input-ui${readOnly ? (readerMode ? "-reader" : "-read-only") : ""}.json`;
const persist = () =>
  writeFileSync(file, JSON.stringify(report, null, 2), { mode: 0o600 });
const browser = await chromium.launch();
let phase = "login",
  page;
try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  page = await context.newPage();
  page.setDefaultTimeout(30000);
  let browserErrors = 0;
  page.on("pageerror", () => browserErrors++);
  await page.goto(origin + "/workspace/data", { timeout: 90000 });
  await page
    .getByRole("textbox", { name: "邮箱", exact: true })
    .fill(readerMode ? reader.email : "qtrade-integration@example.test");
  await page
    .getByLabel("密码", { exact: true })
    .fill(readerMode ? reader.password : admin.password);
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
  await page.getByLabel("切换工作区", { exact: true }).selectOption(scope);
  await page.getByRole("button", { name: "切换", exact: true }).click();
  await expect(page).toHaveURL(origin + "/", { timeout: 60000 });
  if (readerMode) {
    phase = "reader UI restriction";
    await page.goto(origin + "/workspace/data", { timeout: 90000 });
    await expect(
      page
        .locator("#independent-collector")
        .getByRole("button", { name: "预览独立采集计划", exact: true }),
    ).toHaveCount(0);
    await expect(page.locator("#independent-collector")).toContainText(
      "当前为只读访问",
    );
    phase = "reader API restriction";
    const state = await (
      await page.request.get("http://127.0.0.1:3122/auth/session")
    ).json();
    const denied = await page.request.post(`${root}/preview`, {
      headers: { "X-CSRF-Token": state.csrf_token },
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
  for (const kind of ["bars", "factors"]) {
    phase = kind + " selection";
    let run =
      readOnly || resume ? report.runs.find((r) => r.kind === kind) : null;
    if (readOnly) assert(run?.job_id);
    await page.goto(
      origin +
        "/workspace/data" +
        (run?.job_id ? `?collector_job=${run.job_id}` : ""),
      { timeout: 90000 },
    );
    const panel = page.locator("#independent-collector");
    if (!run) {
      await panel
        .getByLabel("采集数据类别", { exact: true })
        .selectOption(kind);
      await panel
        .getByLabel("采集数据来源", { exact: true })
        .selectOption(
          kind === "bars" ? "mairui.history.bars" : "mairui.factor.history",
        );
      await panel
        .getByLabel("独立采集周期", { exact: true })
        .selectOption("1d");
      await panel.getByLabel("独立采集标的", { exact: true }).fill("601288.SH");
      await panel
        .getByLabel("独立采集开始日期", { exact: true })
        .fill(kind === "bars" ? "2025-01-01" : "2025-09-01");
      await panel
        .getByLabel("独立采集结束日期", { exact: true })
        .fill(kind === "bars" ? "2025-12-31" : "2025-09-05");
      await panel
        .getByLabel("独立采集下载方式", { exact: true })
        .selectOption("all");
      if (kind === "factors") {
        while (await panel.getByRole("button", { name: /^移除因子 / }).count())
          await panel
            .getByRole("button", { name: /^移除因子 / })
            .first()
            .click();
        for (const factor of ["pe_ttm", "roe_ttm"]) {
          const card = panel.locator(`[data-factor-id="${factor}"]`);
          await card.getByRole("checkbox").check();
        }
        await expect(
          panel.getByLabel("已选因子").getByRole("button"),
        ).toHaveCount(2);
      }
      const current = panel.locator("[data-collector-job]");
      const previous = (await current.count())
        ? await current.getAttribute("data-collector-job")
        : null;
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
      await expect
        .poll(
          () =>
            panel
              .locator("[data-collector-job]")
              .getAttribute("data-collector-job"),
          { timeout: 90000 },
        )
        .not.toBe(previous);
      const id = await panel
        .locator("[data-collector-job]")
        .getAttribute("data-collector-job");
      run = {
        job_id: id,
        kind,
        expected_records: kind === "bars" ? 243 : 10,
      };
      report.runs.push(run);
      persist();
    }
    const id = run.job_id;
    phase = kind + " worker and publication";
    let job;
    await expect
      .poll(
        async () => {
          const response = await page.request.get(`${root}/jobs/${id}`);
          assert.equal(response.status(), 200);
          job = await response.json();
          return job.state;
        },
        { timeout: 180000, intervals: [1000, 2000] },
      )
      .toBe("completed");
    if (kind === "bars")
      await expect
        .poll(
          async () => {
            job = await (await page.request.get(`${root}/jobs/${id}`)).json();
            return job.parts.every((p) => p.consumer?.state === "visible");
          },
          { timeout: 90000 },
        )
        .toBe(true);
    assert.equal(job.parts.length, kind === "bars" ? 1 : 2);
    assert.deepEqual(
      job.request.fields,
      kind === "bars" ? [] : ["pe_ttm", "roe_ttm"],
    );
    assert.equal(
      job.parts.reduce((sum, p) => sum + p.publication.record_count, 0),
      run.expected_records,
    );
    assert(job.parts.every((p) => p.publication.raw_record_count > 0));
    run.parts = job.parts.map((p) => ({
      part_id: p.part_id,
      publication_id: p.publication.publication_id,
      records: p.publication.record_count,
      field: p.field,
    }));
    run.state = job.state;
    persist();
    phase = kind + " browse and refresh";
    for (const viewport of [
      { width: 1440, height: 1000 },
      { width: 390, height: 844 },
    ]) {
      await page.setViewportSize(viewport);
      await page.reload({ timeout: 90000 });
      await expect(panel.locator("[data-collector-job]")).toHaveAttribute(
        "data-collector-job",
        id,
        { timeout: 90000 },
      );
      for (const part of job.parts) {
        const row = panel.locator(`[data-collector-part="${part.part_id}"]`);
        await row
          .getByRole("button", {
            name: kind === "bars" ? "查看行情记录" : "查看因子数据",
            exact: true,
          })
          .focus();
        await page.keyboard.press("Enter");
        const preview = panel.getByLabel("采集数据预览", { exact: true });
        await expect(preview.locator("tbody tr")).toHaveCount(
          Math.min(100, part.publication.record_count),
          { timeout: 30000 },
        );
        if (kind === "factors") await expect(preview).toContainText(part.field);
      }
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      );
      assert.equal(
        (
          await new AxeBuilder({ page })
            .include("#independent-collector")
            .analyze()
        ).violations.length,
        0,
      );
    }
    run.refresh_verified = true;
    run.desktop_mobile_axe = 0;
    run.keyboard_browse = true;
    persist();
    if (kind === "bars") {
      phase = "frozen chart";
      const link = panel
        .getByRole("link", { name: "终端可见 · 查看图表", exact: true })
        .first();
      await link.focus();
      await page.keyboard.press("Enter");
      await expect(page.locator("#market-chart-heading")).toContainText(
        "农业银行",
        { timeout: 30000 },
      );
      assert.equal(
        new URL(page.url()).searchParams.get("dataset_id"),
        job.parts[0].consumer.dataset_id,
      );
      await page.reload({ timeout: 90000 });
      await expect(page.locator(".market-chart svg")).toBeVisible();
      const chart = await page.request.get(
        `http://127.0.0.1:3122/api/v1/workspaces/${scope}/market-watch/chart?instrument_id=601288.SH&granularity=1d&days=30&dataset_id=${job.parts[0].consumer.dataset_id}`,
      );
      assert.equal(chart.status(), 200);
      phase = "frozen chart window and source binding";
      const bars = (await chart.json()).bars;
      const source = [];
      let offset = 0;
      while (true) {
        const response = await page.request.get(
          `${root}/publications/${job.parts[0].publication.publication_id}/records?offset=${offset}`,
        );
        assert.equal(response.status(), 200);
        const data = await response.json();
        source.push(...data.records);
        if (data.next_offset === null) break;
        offset = data.next_offset;
      }
      assert.equal(source.length, 243);
      const until =
        Math.max(...source.map((r) => Date.parse(r.timestamp))) + 86400000;
      const expected = source.filter(
        (r) => Date.parse(r.timestamp) >= until - 30 * 86400000,
      );
      assert.equal(bars.length, expected.length);
      assert.deepEqual(
        bars.map((r) => r.timestamp),
        expected.map((r) => r.timestamp),
      );
      assert(
        bars.every((r) => r.dataset_id === job.parts[0].consumer.dataset_id),
      );
      assert(
        bars.every((r, i) => Number(r.close) === Number(expected[i].close)),
      );
      run.chart_window_records = bars.length;
      run.frozen_chart = true;
      persist();
      await page.setViewportSize({ width: 1440, height: 1000 });
    }
  }
  assert.equal(browserErrors, 0);
  if (report.failed_phase) {
    report.previous_failure = report.failed_phase;
    delete report.failed_phase;
  }
  report.completed_at = new Date().toISOString();
  persist();
  console.log(
    "PASS prepared downloads: 243 daily bars and two selected factors/10 values, real Worker/RAW/quality/publication, frozen chart, browse/refresh and desktop/mobile axe0",
  );
} catch (error) {
  report.failed_phase = phase;
  persist();
  console.error(
    `FAIL prepared download UI at ${phase}: ${error?.name ?? "Error"}`,
  );
  if (
    page &&
    !page.isClosed() &&
    new URL(page.url()).pathname === "/workspace/data"
  )
    await page
      .screenshot({
        path: "../../.tools/verification-local/review/normalization-input-ui-diagnostic.png",
      })
      .catch(() => {});
  process.exitCode = 1;
} finally {
  await browser.close();
}
