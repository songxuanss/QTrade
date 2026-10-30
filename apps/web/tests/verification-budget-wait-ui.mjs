import assert from "node:assert/strict";
import { readFileSync, writeFileSync, chmodSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
const mode = process.argv[2];
assert(["wait", "observe", "resume"].includes(mode));
const statefile = "../../.tools/verification-local/budget-scope.json";
const state = JSON.parse(readFileSync(statefile));
const identity = JSON.parse(readFileSync("../../.tools/sso-local/state.json"));
const browser = await chromium.launch();
const checks = [];
try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  const page = await context.newPage();
  await page.goto("http://127.0.0.1:13000/workspace/data", { timeout: 90000 });
  await page
    .getByRole("textbox", { name: "邮箱", exact: true })
    .fill("qtrade-integration@example.test");
  await page.getByLabel("密码", { exact: true }).fill(identity.password);
  await page.getByRole("button", { name: "登 录", exact: true }).click();
  await page.waitForURL(
    (u) => u.pathname === "/workspace/data" || u.pathname === "/auth/consent",
    { timeout: 90000 },
  );
  if (page.url().includes("/auth/consent"))
    await page.getByRole("button", { name: "同意授权", exact: true }).click();
  await page.waitForURL((u) => u.pathname === "/workspace/data", {
    timeout: 90000,
  });
  let session = await (
    await page.request.get("http://127.0.0.1:3122/auth/session")
  ).json();
  const rootFor = (id) =>
    `http://127.0.0.1:3122/api/v1/workspaces/${id}/data/collector`;
  const draft = (id) => ({
    source: "mairui.pro.bars",
    dataset_kind: "bars",
    instrument_ids: [id],
    snapshot_ids: [],
    granularity: "5m",
    start: "2025-08-31T16:00:00Z",
    end: "2025-09-01T16:00:00Z",
    fields: [],
    mode: "all",
  });
  async function poll(root, id, wanted) {
    const end = Date.now() + 180000;
    while (Date.now() < end) {
      const r = await page.request.get(`${root}/jobs/${id}`);
      assert.equal(r.status(), 200);
      const j = await r.json();
      if (wanted(j)) return j;
      assert(
        !["failed", "no_data"].includes(j.state),
        `unexpected job terminal ${j.state}`,
      );
      await page.waitForTimeout(1000);
    }
    throw new Error("business result deadline");
  }
  if (mode === "wait") {
    // Warm existing global metadata from the ordinary fixture scope. No usage
    // rows are injected; this bounded preparation uses the actual API/Worker.
    const root = rootFor(session.workspace_id);
    const preview = await page.request.post(root + "/preview", {
      headers: { "X-CSRF-Token": session.csrf_token },
      data: draft("600519.SH"),
    });
    assert.equal(preview.status(), 200);
    const planned = await preview.json();
    const accepted = await page.request.post(root + "/jobs", {
      headers: { "X-CSRF-Token": session.csrf_token },
      data: {
        request: planned.request,
        coverage_version: planned.coverage_version,
      },
    });
    assert.equal(accepted.status(), 201);
    await poll(
      root,
      (await accepted.json()).job_id,
      (j) => j.state === "completed",
    );
  }
  await page
    .getByLabel("切换工作区", { exact: true })
    .selectOption(state.scope_id);
  await page.getByRole("button", { name: "切换", exact: true }).click();
  await expect(page).toHaveURL("http://127.0.0.1:13000/", { timeout: 60000 });
  await page.goto("http://127.0.0.1:13000/workspace/data", { timeout: 90000 });
  session = await (
    await page.request.get("http://127.0.0.1:3122/auth/session")
  ).json();
  assert.equal(session.workspace_id, state.scope_id);
  const root = rootFor(state.scope_id);
  const panel = page.locator("#independent-collector");
  await expect(
    page.getByRole("region", { name: "采集用量", exact: true }),
  ).toHaveAttribute("data-usage-scope", state.scope_id, { timeout: 60000 });
  async function submit(id) {
    const current = panel.locator("[data-collector-job]");
    const previous = (await current.count())
      ? await current.getAttribute("data-collector-job")
      : null;
    await panel
      .getByLabel("采集数据类别", { exact: true })
      .selectOption("bars");
    await panel
      .getByLabel("采集数据来源", { exact: true })
      .selectOption("mairui.pro.bars");
    await panel.getByLabel("独立采集周期", { exact: true }).selectOption("5m");
    await panel.getByLabel("独立采集标的", { exact: true }).fill(id);
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
    await expect(panel.getByLabel("独立采集计划", { exact: true })).toBeVisible(
      { timeout: 60000 },
    );
    await panel
      .getByRole("button", { name: "提交独立采集任务", exact: true })
      .click();
    await expect(panel.locator("[data-collector-job]")).toBeVisible({
      timeout: 60000,
    });
    if (previous)
      await expect(current).not.toHaveAttribute(
        "data-collector-job",
        previous,
        { timeout: 60000 },
      );
    return panel
      .locator("[data-collector-job]")
      .getAttribute("data-collector-job");
  }
  if (mode === "wait" || mode === "observe") {
    const usage = await (await page.request.get(root + "/usage")).json();
    assert.equal(usage.scope_budget.daily_limit, 1);
    if (mode === "observe") {
      const jobs = await (await page.request.get(root + "/jobs")).json();
      const first = jobs.find(
        (j) =>
          j.request.instrument_ids.includes("600000.SH") &&
          j.state === "completed",
      );
      const waiting = jobs.find(
        (j) =>
          j.request.instrument_ids.includes("600036.SH") &&
          j.parts.some((p) => p.wait_reason === "scope_daily"),
      );
      assert(first && waiting, "authoritative existing runs missing");
      state.first_job = first.job_id;
      state.wait_job = waiting.job_id;
    } else {
      assert.equal(usage.scope_budget.credits_used, 0);
      state.first_job = await submit("600000.SH");
    }
    const first = await poll(
      root,
      state.first_job,
      (j) =>
        j.state === "completed" &&
        j.parts.every((p) => p.consumer?.state === "visible"),
    );
    assert(first.parts[0].publication.record_count > 0);
    state.first_publication = first.parts[0].publication;
    const spent = await (await page.request.get(root + "/usage")).json();
    assert.equal(spent.scope_budget.credits_used, 1);
    assert(spent.success_count >= 1);
    if (mode === "wait") state.wait_job = await submit("600036.SH");
    else
      await page.goto(
        `http://127.0.0.1:13000/workspace/data?collector_job=${state.wait_job}`,
        { timeout: 90000 },
      );
    const waiting = await poll(root, state.wait_job, (j) =>
      j.parts.some((p) => p.wait_reason === "scope_daily"),
    );
    assert.equal(waiting.parts[0].state, "waiting");
    assert.equal(waiting.parts[0].attempt, 0);
    assert.equal(waiting.parts[0].publication, null);
    await expect(panel).toContainText("当前空间日信用不足", { timeout: 30000 });
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 1000 });
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
      const shot = `../../.tools/verification-local/review/budget-wait-${width}.png`;
      await panel.screenshot({ path: shot });
      chmodSync(shot, 0o600);
    }
    await page.reload({ timeout: 90000 });
    await page.goto(
      `http://127.0.0.1:13000/workspace/data?collector_job=${state.wait_job}`,
      { timeout: 90000 },
    );
    await expect(panel).toContainText("当前空间日信用不足", { timeout: 60000 });
    checks.push(
      "dedicated actual scope / successful first RPC+publish / second zero-call credit rejection / persisted wait / desktop mobile axe0 / refresh",
    );
    state.wait_checks = checks;
    writeFileSync(statefile, JSON.stringify(state));
    chmodSync(statefile, 0o600);
  } else {
    const usage = await (await page.request.get(root + "/usage")).json();
    assert.equal(usage.scope_budget.daily_limit, 2000);
    await page.goto(
      `http://127.0.0.1:13000/workspace/data?collector_job=${state.wait_job}`,
      { timeout: 90000 },
    );
    await expect(
      panel.getByRole("button", { name: "提前重试预算等待", exact: true }),
    ).toBeVisible({ timeout: 60000 });
    await panel
      .getByRole("button", { name: "提前重试预算等待", exact: true })
      .focus();
    await page.keyboard.press("Enter");
    const complete = await poll(
      root,
      state.wait_job,
      (j) =>
        j.state === "completed" &&
        j.parts.every((p) => p.consumer?.state === "visible"),
    );
    assert(complete.parts[0].publication.record_count > 0);
    assert.equal(complete.parts[0].wait_reason, null);
    const first = await (
      await page.request.get(`${root}/jobs/${state.first_job}`)
    ).json();
    assert.deepEqual(first.parts[0].publication, state.first_publication);
    await page.reload({ timeout: 90000 });
    await expect(panel.locator("[data-collector-job]")).toHaveAttribute(
      "data-collector-job",
      state.wait_job,
    );
    await expect(panel).toContainText("招商银行", { timeout: 30000 });
    await panel
      .getByRole("link", { name: "终端可见 · 查看图表", exact: true })
      .first()
      .click();
    await expect(page.locator("#market-chart-heading")).toContainText(
      "招商银行 · 600036.SH",
      { timeout: 30000 },
    );
    await expect(page.locator(".market-chart svg")).toBeVisible({
      timeout: 30000,
    });
    await page.reload({ timeout: 90000 });
    await expect(page.locator("#market-chart-heading")).toContainText(
      "招商银行",
    );
    const file =
      "../../.tools/verification-local/review/budget-wait-ui-results.json";
    writeFileSync(
      file,
      JSON.stringify(
        {
          origin: "http://127.0.0.1:13000",
          scope_id: state.scope_id,
          integration:
            "actual SSO/API/PostgreSQL/Worker/MaiRui/archives/market",
          checks: [
            ...state.wait_checks,
            "restored quota / keyboard retry / real second source response+nonempty publish / market visible / prior publication unchanged",
          ],
          jobs: [
            { id: state.first_job, publications: [state.first_publication] },
            {
              id: state.wait_job,
              publications: complete.parts.map((p) => p.publication),
            },
          ],
        },
        null,
        2,
      ),
    );
    chmodSync(file, 0o600);
  }
  await context.close();
  console.log(`PASS budget UI phase ${mode}`);
} finally {
  await browser.close();
}
