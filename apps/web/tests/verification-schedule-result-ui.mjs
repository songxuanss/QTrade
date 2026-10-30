import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const state = JSON.parse(
  readFileSync("../../.tools/verification-local/schedule-live.json"),
);
const identity = JSON.parse(readFileSync("../../.tools/sso-local/state.json"));
assert.equal(state.disabled, true);
assert.equal(state.result.state, "completed");
const origin = "http://127.0.0.1:13000";
const api = "http://127.0.0.1:3122";
const browser = await chromium.launch();
const checks = [];
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
    (u) => u.pathname === "/workspace/data" || u.pathname === "/auth/consent",
    { timeout: 90000 },
  );
  if (page.url().includes("/auth/consent"))
    await page.getByRole("button", { name: "同意授权", exact: true }).click();
  await expect(page).toHaveURL(`${origin}/workspace/data`, { timeout: 90000 });
  await page
    .getByLabel("切换工作区", { exact: true })
    .selectOption(state.scope_id);
  await page.getByRole("button", { name: "切换", exact: true }).click();
  await expect(page).toHaveURL(`${origin}/`, { timeout: 60000 });
  await page.goto(`${origin}/workspace/data`, { timeout: 90000 });
  const panel = page.locator("#independent-collector");
  const recent = panel.locator(".collector-recent");
  await recent.locator("summary").focus();
  await page.keyboard.press("Enter");
  const result = recent.getByRole("button", { name: /农业银行.*1d/ });
  await expect(result).toHaveCount(1, { timeout: 60000 });
  await result.focus();
  await page.keyboard.press("Enter");
  await expect(panel.locator("[data-collector-job]")).toHaveAttribute(
    "data-collector-job",
    state.result.job_id,
  );
  await expect(
    panel.getByRole("button", { name: "查看行情记录", exact: true }),
  ).toBeVisible();
  await panel
    .getByRole("button", { name: "查看行情记录", exact: true })
    .click();
  await expect(panel.getByLabel("采集数据预览")).toContainText("601288.SH");
  const root = `${api}/api/v1/workspaces/${state.scope_id}`;
  let job;
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `${root}/data/collector/jobs/${state.result.job_id}`,
        );
        assert.equal(response.status(), 200);
        job = await response.json();
        return job.parts.every((p) => p.consumer?.state === "visible");
      },
      { timeout: 90000 },
    )
    .toBe(true);
  await page.reload({ timeout: 90000 });
  await expect(panel.locator("[data-collector-job]")).toHaveAttribute(
    "data-collector-job",
    state.result.job_id,
  );
  const link = panel.getByRole("link", {
    name: "终端可见 · 查看图表",
    exact: true,
  });
  await link.click();
  await expect(page.locator("#market-chart-heading")).toContainText(
    "农业银行 · 601288.SH",
  );
  await expect(page.locator(".market-chart svg")).toBeVisible();
  assert.equal(
    new URL(page.url()).searchParams.get("dataset_id"),
    state.result.publications[0],
  );
  await page.reload({ timeout: 90000 });
  const chart = await page.request.get(
    `${root}/market-watch/chart?instrument_id=601288.SH&granularity=1d&days=30&dataset_id=${state.result.publications[0]}`,
  );
  assert.equal(chart.status(), 200);
  const body = await chart.json();
  assert.equal(body.bars.length, state.result.rows);
  assert.deepEqual(
    [...new Set(body.bars.map((b) => b.dataset_id))],
    state.result.publications,
  );
  checks.push(
    "script-created schedule > deployed timer > real Worker/source > exact UI result > named frozen chart > refresh",
  );
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    await expect(page.locator(".market-chart svg")).toBeVisible();
    if (width === 390) {
      const plot = page.getByRole("region", {
        name: "OHLC 蜡烛图与成交量",
        exact: true,
      });
      await plot.focus();
      for (let i = 0; i < 24; i++) await page.keyboard.press("ArrowRight");
      await expect
        .poll(() =>
          plot.evaluate(
            (node) =>
              node.scrollLeft + node.clientWidth >= node.scrollWidth - 1,
          ),
        )
        .toBe(true);
      checks.push("mobile keyboard pans the local chart to its latest bars");
    }
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    assert.equal(
      (await new AxeBuilder({ page }).analyze()).violations.length,
      0,
    );
    await page.screenshot({
      path: `../../.tools/verification-local/review/schedule-result-${width}.png`,
      fullPage: true,
    });
  }
  checks.push("desktop/mobile chart, axe0 and no page overflow");
  writeFileSync(
    "../../.tools/verification-local/review/schedule-result-ui.json",
    JSON.stringify(
      {
        checks,
        job_id: state.result.job_id,
        publications: state.result.publications,
        rows: body.bars.length,
      },
      null,
      2,
    ),
  );
  console.log(
    `PASS scheduled result UI: ${checks.length} checks, ${body.bars.length} rows`,
  );
} finally {
  await browser.close();
}
