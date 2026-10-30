import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
const state = JSON.parse(readFileSync("../../.tools/sso-local/state.json"));
const origin = "http://127.0.0.1:3120",
  api = "http://127.0.0.1:3122";
const directory = "../../.tools/verification-local/review";
const browser = await chromium.launch();
try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${origin}/workspace/data`);
  await page
    .getByRole("textbox", { name: "邮箱", exact: true })
    .fill("qtrade-integration@example.test");
  await page.getByLabel("密码", { exact: true }).fill(state.password);
  await page.getByRole("button", { name: "登 录", exact: true }).click();
  await page.waitForURL(/3120\/workspace\/data|3121\/auth\/consent/);
  if (page.url().includes("/auth/consent"))
    await page.getByRole("button", { name: "同意授权", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "数据管理", exact: true }),
  ).toBeVisible();
  const session = await (await page.request.get(`${api}/auth/session`)).json();
  const root = `${api}/api/v1/workspaces/${session.workspace_id}`;
  const tasks = await (
    await page.request.get(`${root}/data/tasks?limit=200`)
  ).json();
  const task = tasks.find(
    (item) =>
      item.status === "completed" &&
      item.instrument_id === "600519.SH" &&
      item.start === "2026-09-28T01:30:00+00:00" &&
      item.end === "2026-09-28T07:00:00+00:00",
  );
  assert(task, "Run actual download verification first");
  await page
    .getByLabel("已完成下载", { exact: true })
    .selectOption(task.task_id);
  await page
    .getByRole("button", { name: "生成 1 分钟研究数据", exact: true })
    .click();
  await expect(page.getByRole("status")).toContainText("研究数据已生成");
  await page.reload();
  let datasets = await (await page.request.get(`${root}/data/datasets`)).json();
  const dataset = datasets.find(
    (item) =>
      item.instrument_ids.includes("600519.SH") &&
      new Date(item.start).getTime() === new Date(task.start).getTime() &&
      new Date(item.end).getTime() === new Date(task.end).getTime(),
  );
  assert(dataset && dataset.granularity === "1m");
  await expect(
    page.locator(`[data-dataset-id="${dataset.dataset_id}"]`),
  ).toContainText("600519.SH");
  const count = datasets.length;
  await page
    .getByLabel("已完成下载", { exact: true })
    .selectOption(task.task_id);
  await page
    .getByRole("button", { name: "生成 1 分钟研究数据", exact: true })
    .click();
  await expect(page.getByRole("status")).toContainText("研究数据已生成");
  assert.equal(
    (await (await page.request.get(`${root}/data/datasets`)).json()).length,
    count,
  );
  await page
    .locator(`[data-dataset-id="${dataset.dataset_id}"]`)
    .getByRole("link", { name: "查看历史行情", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "市场终端", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "1m", exact: true }).click();
  await expect(page.locator(".market-chart-legend")).toContainText("根 Bar");
  const chart = await (
    await page.request.get(
      `${root}/market-watch/chart?instrument_id=600519.SH&granularity=1m&days=7`,
    )
  ).json();
  assert(
    chart.bars.length > 100 &&
      chart.bars.every((bar) => bar.dataset_id === dataset.dataset_id),
  );
  await expect(page.locator(".market-svg-scroll svg")).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("button", { name: "显示 600519.SH K 线图", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "15m", exact: true }).click();
  await expect(
    page.getByText("该周期暂无可用行情数据", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "1m", exact: true }).click();
  await expect(page.locator(".market-chart-legend")).toContainText("根 Bar");
  for (const [label, width, height] of [
    ["desktop", 1440, 1000],
    ["mobile", 390, 844],
  ]) {
    await page.setViewportSize({ width, height });
    assert.deepEqual((await new AxeBuilder({ page }).analyze()).violations, []);
    await page.screenshot({
      path: `${directory}/research-market-${label}.png`,
      fullPage: true,
    });
    const overflow = await page.evaluate(() => ({
      width: innerWidth,
      document: document.documentElement.scrollWidth,
      outside: [...document.querySelectorAll("main *")]
        .filter(
          (element) =>
            element instanceof HTMLElement &&
            element.getBoundingClientRect().right > innerWidth + 1,
        )
        .map((element) => ({
          tag: element.tagName,
          class: element.className,
          right: element.getBoundingClientRect().right,
        }))
        .slice(0, 20),
    }));
    assert(overflow.document <= overflow.width, JSON.stringify(overflow));
  }
  await page
    .getByRole("button", { name: "移除 600519.SH", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "移除 600519.SH", exact: true }),
  ).toHaveCount(0);
  await page.reload();
  // The URL intentionally re-adds this requested instrument; visit the plain saved watchlist.
  await page.goto(`${origin}/workspace/market`);
  await page
    .getByRole("button", { name: "移除 600519.SH", exact: true })
    .click();
  await page.reload();
  await expect(
    page.getByRole("button", { name: "移除 600519.SH", exact: true }),
  ).toHaveCount(0);
  await page
    .getByLabel("添加关注标的", { exact: true })
    .selectOption("600519.SH");
  await page.getByRole("button", { name: "加入自选", exact: true }).click();
  await page.reload();
  await expect(
    page.getByRole("button", { name: "显示 600519.SH K 线图", exact: true }),
  ).toBeVisible();
  assert.deepEqual(errors, []);
  writeFileSync(
    `${directory}/research-data-results.txt`,
    `PASS: actual UI publication/retry, Timescale historical query, chart periods, saved watchlist add/remove/reload, desktop/mobile axe.\nDataset: ${dataset.dataset_id}\nBars: ${chart.bars.length}\n`,
  );
  console.log(
    "UI completed ticks -> immutable minutes -> actual market chart: PASS",
  );
} finally {
  await browser.close();
}
