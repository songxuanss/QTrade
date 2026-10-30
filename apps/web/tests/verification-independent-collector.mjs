import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
const identity = JSON.parse(readFileSync("../../.tools/sso-local/state.json"));
const origin =
    process.env.QTRADE_VERIFICATION_ORIGIN ?? "http://127.0.0.1:3120",
  api = process.env.QTRADE_VERIFICATION_API ?? "http://127.0.0.1:3122";
const barPeriod = process.env.QTRADE_VERIFICATION_BAR_PERIOD ?? "5m";
const confirmSmall = process.argv.includes("--confirm-small");
assert(["1m", "5m"].includes(barPeriod));
// Fixed live 2025-09 samples: 1m has one extra 09:30 source observation
// per day. Do not discard it or infer an auction from the record count.
const expectedSingle = barPeriod === "1m" ? 1928 : 384;
const expectedPool = barPeriod === "1m" ? 482 : 96;
const resultSuffix =
  (barPeriod === "1m" ? "-1m" : "") + (confirmSmall ? "-confirmed" : "");
const browser = await chromium.launch();
const results = {
  origin,
  api,
  bar_period: barPeriod,
  confirmed_small: confirmSmall,
  integration:
    "live provider / independent PostgreSQL / Parquet / HTTP / market",
  jobs: [],
  checks: [],
};
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
    (url) =>
      url.href === `${origin}/workspace/data` ||
      url.pathname === "/auth/consent",
    {
      timeout: 90000,
    },
  );
  if (page.url().includes("/auth/consent"))
    await page.getByRole("button", { name: "同意授权", exact: true }).click();
  await expect(page).toHaveURL(`${origin}/workspace/data`, { timeout: 90000 });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const session = await (await page.request.get(`${api}/auth/session`)).json();
  const root = `${api}/api/v1/workspaces/${session.workspace_id}`;
  const csrf = { "X-CSRF-Token": session.csrf_token };
  let panel = page.locator("#independent-collector");
  if (process.argv.includes("--unavailable-check")) {
    await expect(panel).toContainText("独立采集服务尚未连接", {
      timeout: 30000,
    });
    await expect(
      panel.getByRole("button", { name: "预览独立采集计划" }),
    ).toHaveCount(0);
    await expect(page.locator("#collection")).toBeVisible();
    writeFileSync(
      "../../.tools/verification-local/review/independent-collector-outage.json",
      JSON.stringify(
        {
          integration: "controlled real service outage",
          outcome:
            "explicit unavailability; no new task form; old tasks readable",
        },
        null,
        2,
      ),
    );
    console.log(
      "PASS controlled collector outage visible in UI; legacy tasks retained",
    );
    await browser.close();
    process.exit(0);
  }
  await expect(panel).toContainText("远程服务已连接", { timeout: 90000 });
  async function plan(kind, mode = "all", last = "2025-09-10") {
    await panel.getByLabel("采集数据类别", { exact: true }).selectOption(kind);
    if (kind === "bars") {
      await panel
        .getByLabel("采集数据来源", { exact: true })
        .selectOption("mairui.pro.bars");
      await panel
        .getByLabel("独立采集周期", { exact: true })
        .selectOption(barPeriod);
    }
    await panel.getByLabel("独立采集标的", { exact: true }).fill("600519.SH");
    await panel
      .getByLabel("独立采集开始日期", { exact: true })
      .fill("2025-09-01");
    await panel.getByLabel("独立采集结束日期", { exact: true }).fill(last);
    await panel
      .getByLabel("独立采集下载方式", { exact: true })
      .selectOption(mode);
    await panel
      .getByRole("button", { name: "预览独立采集计划", exact: true })
      .click();
    await expect(panel.getByLabel("独立采集计划", { exact: true })).toBeVisible(
      { timeout: 90000 },
    );
  }
  async function complete(kind) {
    if (confirmSmall) {
      const ack = panel.getByRole("checkbox", {
        name: "确认计划调用规模",
        exact: true,
      });
      await expect(ack).not.toBeChecked();
      await expect(
        panel.getByRole("button", { name: "提交独立采集任务", exact: true }),
      ).toBeDisabled();
      await ack.focus();
      await page.keyboard.press("Space");
    }
    const current = panel.locator("[data-collector-job]");
    const previous = (await current.count())
      ? await current.getAttribute("data-collector-job")
      : null;
    await panel
      .getByRole("button", { name: "提交独立采集任务", exact: true })
      .click();
    await expect(panel.locator("[data-collector-job]")).toBeVisible({
      timeout: 90000,
    });
    await expect
      .poll(() => current.getAttribute("data-collector-job"), {
        timeout: 90000,
      })
      .not.toBe(previous);
    const id = await panel
      .locator("[data-collector-job]")
      .getAttribute("data-collector-job");
    let job;
    const deadline = Date.now() + 300000;
    while (Date.now() < deadline) {
      const response = await page.request.get(
        `${root}/data/collector/jobs/${id}`,
      );
      assert.equal(response.status(), 200);
      job = await response.json();
      assert.notEqual(job.state, "failed", "live collection failed");
      if (
        job.state === "completed" &&
        (kind === "factors" ||
          job.parts.every((p) => p.consumer?.state === "visible"))
      )
        break;
      await page.waitForTimeout(1500);
    }
    assert.equal(job.state, "completed");
    assert.ok(
      job.parts.every(
        (p) =>
          p.publication?.raw_record_count > 0 &&
          p.publication?.record_count > 0,
      ),
    );
    if (kind === "bars")
      assert.ok(
        job.parts.every((p) => p.consumer?.state === "visible"),
        "market import pending/failed",
      );
    results.jobs.push({
      id,
      kind,
      parts: job.total_parts,
      records: job.parts.reduce(
        (sum, p) => sum + p.publication.record_count,
        0,
      ),
      publications: job.parts.map((p) => ({
        id: p.publication.publication_id,
        records: p.publication.record_count,
        rawSha256: p.publication.raw_sha256,
        name: p.publication.instrument_name,
      })),
    });
    await page.reload({ timeout: 90000 });
    panel = page.locator("#independent-collector");
    await expect(panel.locator(`[data-collector-job="${id}"]`)).toBeVisible({
      timeout: 90000,
    });
    await expect(panel).toContainText("贵州茅台");
    return job;
  }
  await plan("bars");
  await expect(panel.getByLabel("独立采集计划")).toContainText(
    "贵州茅台 · 600519.SH",
  );
  const bars = await complete("bars");
  assert.equal(bars.total_parts, 1);
  if (barPeriod === "1m") {
    assert.equal(
      bars.parts[0].publication.evidence.opening_boundary_source_labels.length,
      8,
    );
    assert.equal(
      bars.parts[0].publication.evidence.opening_boundary_semantics,
      "unverified",
    );
    assert(
      bars.parts[0].publication.limitations.some((text) =>
        text.includes("09:30额外观测"),
      ),
    );
  }
  assert.equal(results.jobs[0].records, expectedSingle);
  assert.ok(
    bars.parts.every((p) => new Date(p.end) - new Date(p.start) > 7 * 86400000),
  );
  await panel
    .getByRole("button", { name: "查看行情记录", exact: true })
    .first()
    .click();
  await expect(
    panel.getByLabel("采集数据预览").locator("tbody tr").first(),
  ).toBeVisible();
  if (barPeriod === "1m")
    await expect(panel.getByLabel("采集数据预览")).toContainText(
      "09:30额外观测",
    );
  await panel
    .getByRole("link", { name: "终端可见 · 查看图表", exact: true })
    .first()
    .click();
  await expect(page.locator("#market-chart-heading")).toContainText(
    "贵州茅台 · 600519.SH",
    { timeout: 30000 },
  );
  await expect(page.locator(".market-chart svg")).toBeVisible({
    timeout: 30000,
  });
  await expect(
    page
      .getByLabel("图表周期")
      .getByRole("button", { name: barPeriod, exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.reload({ timeout: 90000 });
  await expect(page.locator("#market-chart-heading")).toContainText("贵州茅台");
  const chartDataset = bars.parts[0].consumer.dataset_id;
  assert.equal(chartDataset, bars.parts[0].publication.publication_id);
  assert.equal(
    new URL(page.url()).searchParams.get("dataset_id"),
    chartDataset,
  );
  await expect(page.locator(".market-chart")).toContainText("冻结发布版本");
  const chartResponse = await page.request.get(
    `${root}/market-watch/chart?instrument_id=600519.SH&granularity=${barPeriod}&days=30&dataset_id=${chartDataset}`,
  );
  assert.equal(chartResponse.status(), 200);
  const chart = await chartResponse.json();
  assert.equal(chart.bars.length, expectedSingle);
  assert.deepEqual(
    [...new Set(chart.bars.map((bar) => bar.dataset_id))].sort(),
    bars.parts.map((part) => part.publication.publication_id).sort(),
  );
  results.checks.push(
    "UI >7 days / one source-aware segment / nonempty archive / market import / named chart / refresh",
  );
  await page.goto(`${origin}/workspace/data`, { timeout: 90000 });
  panel = page.locator("#independent-collector");
  await plan("bars", "missing");
  await expect(panel.getByLabel("独立采集计划")).toContainText(
    "所选范围已有数据或任务",
  );
  await expect(
    panel.locator(".collector-preview .collector-metrics > span").last(),
  ).toHaveText("新增下载 0");
  await plan("bars", "missing", "2025-09-12");
  await expect(
    panel.locator(".collector-preview .collector-metrics > span").last(),
  ).toHaveText("新增下载 1");
  results.checks.push("duplicate and missing-only extension preview");
  await plan("factors", "all", "2025-09-05");
  const factors = await complete("factors");
  assert.equal(factors.total_parts, 1);
  assert.equal(results.jobs[1].records, 5);
  await panel
    .getByRole("button", { name: "查看因子数据", exact: true })
    .click();
  await expect(
    panel.getByLabel("采集数据预览").locator("tbody tr"),
  ).toHaveCount(5);
  await expect(panel.getByLabel("采集数据预览")).toContainText("unknown");
  results.checks.push(
    "UI dynamic factor catalogue / live 5 points / independent browse / refresh",
  );
  for (const viewport of [
    { width: 1440, height: 1000 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(viewport);
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
      "page horizontal overflow",
    );
    const audit = await new AxeBuilder({ page })
      .include("#independent-collector")
      .analyze();
    assert.deepEqual(
      audit.violations.map((v) => ({ id: v.id, impact: v.impact })),
      [],
    );
    await panel.screenshot({
      path: `../../.tools/verification-local/review/independent-collector${resultSuffix}-${viewport.width}.png`,
    });
  }
  results.checks.push("desktop/mobile / overflow / accessibility");
  const rejected = await page.request.post(`${root}/data/collector/preview`, {
    headers: csrf,
    data: {
      source: "mairui.pro.bars",
      dataset_kind: "bars",
      instrument_ids: ["stock:US:XNAS:AAPL"],
      granularity: "5m",
      start: "2025-09-01T00:00:00Z",
      end: "2025-09-02T00:00:00Z",
      fields: [],
      mode: "missing",
    },
  });
  assert.equal(rejected.status(), 400);
  assert.equal(
    (
      await page.request.post(`${root}/data/collector/preview`, { data: {} })
    ).status(),
    403,
  );
  const anonymous = await browser.newContext();
  assert.equal(
    (await anonymous.request.get(`${root}/data/collector/catalog`)).status(),
    401,
  );
  assert.equal(
    (
      await page.request.get(
        `${api}/api/v1/workspaces/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/data/collector/jobs`,
      )
    ).status(),
    404,
  );
  results.checks.push(
    "unsupported market / CSRF / anonymous / scope rejection",
  );
  // A saved, scoped pool is resolved by QTrade before the independent request is frozen.
  await page.setViewportSize({ width: 1440, height: 1000 });
  const poolResponse = await page.request.post(
    `${root}/data/universes/custom`,
    {
      headers: csrf,
      data: {
        name: "独立采集验收股票池",
        instrument_ids: ["600519.SH", "601288.SH"],
      },
    },
  );
  assert.equal(poolResponse.status(), 201);
  const pool = await poolResponse.json();
  await page.goto(`${origin}/workspace/data`, { timeout: 90000 });
  panel = page.locator("#independent-collector");
  await panel.getByLabel("采集数据类别", { exact: true }).selectOption("bars");
  await panel
    .getByLabel("采集数据来源", { exact: true })
    .selectOption("mairui.pro.bars");
  await panel
    .getByLabel("独立采集周期", { exact: true })
    .selectOption(barPeriod);
  await panel
    .getByLabel("独立采集股票池", { exact: true })
    .selectOption(pool.snapshot_id);
  await panel
    .getByLabel("独立采集开始日期", { exact: true })
    .fill("2025-09-01");
  await panel
    .getByLabel("独立采集结束日期", { exact: true })
    .fill("2025-09-01");
  await panel
    .getByLabel("独立采集下载方式", { exact: true })
    .selectOption("all");
  await panel
    .getByRole("button", { name: "预览独立采集计划", exact: true })
    .click();
  await expect(panel.getByLabel("独立采集计划")).toContainText("农业银行", {
    timeout: 90000,
  });
  await complete("bars");
  assert.equal(results.jobs[2].records, expectedPool);
  results.checks.push(
    `saved pool all members / live two stocks / names / ${expectedPool} records / visible after refresh`,
  );

  const approver = JSON.parse(
    readFileSync("../../.tools/verification-local/approver.json"),
  );
  const readonlyContext = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  const reviewer = await readonlyContext.newPage();
  await reviewer.goto(`${origin}/workspace/data`, { timeout: 90000 });
  await reviewer
    .getByRole("textbox", { name: "邮箱", exact: true })
    .fill(approver.email);
  await reviewer.getByLabel("密码", { exact: true }).fill(approver.password);
  await reviewer.getByRole("button", { name: "登 录", exact: true }).click();
  await reviewer.waitForURL(
    (url) =>
      url.href === `${origin}/workspace/data` ||
      url.pathname === "/auth/consent",
    {
      timeout: 90000,
    },
  );
  if (reviewer.url().includes("/auth/consent"))
    await reviewer
      .getByRole("button", { name: "同意授权", exact: true })
      .click();
  await expect(reviewer).toHaveURL(`${origin}/workspace/data`, {
    timeout: 90000,
  });
  let reviewSession = await (
    await reviewer.request.get(`${api}/auth/session`)
  ).json();
  assert.equal(
    (
      await page.request.put(`${root}/members/${reviewSession.user_id}`, {
        headers: csrf,
        data: { role: "risk_manager", active: true },
      })
    ).status(),
    200,
  );
  await reviewer.reload({ timeout: 90000 });
  await reviewer
    .getByLabel("切换工作区", { exact: true })
    .selectOption(session.workspace_id);
  await reviewer.getByRole("button", { name: "切换", exact: true }).click();
  await expect(reviewer).toHaveURL(`${origin}/`);
  await expect(reviewer.locator(".workspace-switch")).toContainText(
    "风险管理员",
  );
  await reviewer.goto(`${origin}/workspace/data`, { timeout: 90000 });
  await reviewer.setViewportSize({ width: 390, height: 844 });
  await expect(reviewer.locator("#independent-collector")).toContainText(
    "当前为只读访问",
  );
  await expect(
    reviewer.getByRole("button", { name: "预览独立采集计划", exact: true }),
  ).toHaveCount(0);
  reviewSession = await (
    await reviewer.request.get(`${api}/auth/session`)
  ).json();
  assert.equal(
    (
      await reviewer.request.post(`${root}/data/collector/preview`, {
        headers: { "X-CSRF-Token": reviewSession.csrf_token },
        data: {},
      })
    ).status(),
    403,
  );
  results.checks.push(
    "read-only mobile UI / current membership / mutation rejected",
  );
  await panel.getByLabel("独立采集股票池", { exact: true }).selectOption("");
  await panel
    .getByLabel("独立采集标的", { exact: true })
    .fill("stock:US:XNAS:AAPL");
  await panel
    .getByLabel("独立采集开始日期", { exact: true })
    .fill("2025-09-01");
  await panel
    .getByLabel("独立采集结束日期", { exact: true })
    .fill("2025-09-02");
  await panel
    .getByRole("button", { name: "预览独立采集计划", exact: true })
    .click();
  await expect(panel.locator("[role=alert]")).toContainText("该来源不支持", {
    timeout: 30000,
  });
  await expect(panel.getByLabel("独立采集计划")).toHaveCount(0);
  results.checks.push("UI unsupported asset rejected before submission");
  assert.deepEqual(errors, []);
  writeFileSync(
    `../../.tools/verification-local/review/independent-collector-results${resultSuffix}.json`,
    JSON.stringify(results, null, 2),
  );
  console.log(
    "PASS independent collector UI E2E:",
    results.checks.length,
    "checks;",
    results.jobs.map((j) => `${j.kind}:${j.records}`).join(", "),
  );
} finally {
  await browser.close();
}
