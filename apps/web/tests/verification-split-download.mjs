import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
const identity = JSON.parse(readFileSync("../../.tools/sso-local/state.json"));
const origin = "http://127.0.0.1:3120",
  api = "http://127.0.0.1:3122";
const browser = await chromium.launch();
try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  const page = await context.newPage();
  await page.goto(`${origin}/workspace/data`);
  await page
    .getByRole("textbox", { name: "邮箱", exact: true })
    .fill("qtrade-integration@example.test");
  await page.getByLabel("密码", { exact: true }).fill(identity.password);
  await page.getByRole("button", { name: "登 录", exact: true }).click();
  await page.waitForURL(/3120\/workspace\/data|3121\/auth\/consent/);
  if (page.url().includes("/auth/consent"))
    await page.getByRole("button", { name: "同意授权", exact: true }).click();
  await expect(page).toHaveURL(`${origin}/workspace/data`);
  const session = await (await page.request.get(`${api}/auth/session`)).json();
  const root = `${api}/api/v1/workspaces/${session.workspace_id}`;
  await page.getByLabel("历史行情股票代码", { exact: true }).fill("601288.SH");
  await page.getByLabel("历史行情周期", { exact: true }).selectOption("1d");
  await page.getByLabel("历史行情开始日期", { exact: true }).fill("2025-09-01");
  await page.getByLabel("历史行情结束日期", { exact: true }).fill("2025-09-10");
  await page
    .getByRole("button", { name: "创建历史行情任务", exact: true })
    .click();
  await page.waitForURL(/result=(?:complete|partial|success)/);
  if (/result=(complete|partial)/.test(page.url()))
    await page
      .getByRole("button", { name: "重新下载全部", exact: true })
      .click();
  await expect(page.locator(".data-notice[role=status]")).toContainText(
    "操作已完成",
  );
  let tasks = await (await page.request.get(`${root}/data/tasks`)).json();
  const groupId = tasks.find(
    (task) =>
      task.instrument_id === "601288.SH" &&
      task.part_count === 2 &&
      task.start.startsWith("2025-08-31"),
  )?.download_group_id;
  assert.ok(groupId, "split group not created through UI");
  const parts = () =>
    tasks
      .filter((task) => task.download_group_id === groupId)
      .sort((a, b) => a.part_index - b.part_index);
  assert.equal(parts().length, 2);
  assert.equal(parts()[0].end, parts()[1].start);
  assert.equal(
    Date.parse(parts()[0].end) - Date.parse(parts()[0].start),
    7 * 86400000,
  );
  assert.equal(
    Date.parse(parts()[1].end) - Date.parse(parts()[1].start),
    3 * 86400000,
  );
  const deadline = Date.now() + 180000;
  while (parts().some((task) => task.status !== "completed")) {
    assert.ok(Date.now() < deadline, "split download did not complete");
    assert.ok(
      parts().every((task) => task.status !== "failed"),
      "split download failed",
    );
    await page.waitForTimeout(1000);
    tasks = await (await page.request.get(`${root}/data/tasks`)).json();
  }
  assert.ok(parts().every((task) => task.page_count > 0 && task.dataset_id));
  const batches = await (
    await page.request.get(`${root}/data/batches?limit=200`)
  ).json();
  for (const task of parts()) {
    const archived = batches.filter(
      (batch) => batch.ingestion_task_id === task.task_id,
    );
    assert.ok(archived.length > 0, "completed task has no persisted raw batch");
    assert.ok(
      archived.some((batch) => batch.record_count > 0),
      "completed task has only empty raw batches",
    );
    assert.ok(
      archived.every((batch) => /^[a-f0-9]{64}$/.test(batch.content_sha256)),
    );
  }
  await page.reload();
  for (const task of parts())
    await expect(
      page.locator(`[data-task-id="${task.task_id}"]`),
    ).toContainText(`自动拆分 ${task.part_index}/2`);
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    assert.deepEqual((await new AxeBuilder({ page }).analyze()).violations, []);
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    await page.screenshot({
      path: `../../.tools/verification-local/review/split-data-${width}.png`,
      fullPage: true,
    });
  }
  await page.goto(`${origin}/workspace/market?instrument=601288.SH`);
  await page
    .getByRole("button", { name: "显示 601288.SH K 线图", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "1d", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".market-chart-legend")).toContainText("根 Bar");
  const response = await page.request.get(
    `${root}/market-watch/chart?instrument_id=601288.SH&granularity=1d&days=30`,
  );
  assert.equal(response.status(), 200);
  const chart = await response.json();
  assert.ok(chart.bars.length > 0, "actual downloaded daily chart is empty");
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    assert.deepEqual((await new AxeBuilder({ page }).analyze()).violations, []);
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    await page.screenshot({
      path: `../../.tools/verification-local/review/split-market-${width}.png`,
      fullPage: true,
    });
  }
  await page.reload();
  await page
    .getByRole("button", { name: "显示 601288.SH K 线图", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "1d", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".market-chart-legend")).toContainText("根 Bar");
  // Normal market entry must work without a deep link or manual period selection.
  await page.goto(`${origin}/workspace/market`);
  await page
    .getByRole("button", { name: "显示 601288.SH K 线图", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "1d", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(
    page.getByRole("button", { name: "1 月", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".market-chart-legend")).toContainText("根 Bar");
  await page.getByRole("button", { name: "1m", exact: true }).click();
  await expect(
    page.getByText("请选择与下载任务一致的周期", { exact: false }),
  ).toBeVisible();
  await page.getByRole("button", { name: "关闭图表", exact: true }).click();
  await page
    .getByRole("button", { name: "显示 601288.SH K 线图", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "1d", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".market-chart-legend")).toContainText("根 Bar");
  writeFileSync(
    "../../.tools/verification-local/review/split-results.txt",
    "PASS: real UI -> atomic 7+3 day group -> MaiRui -> Worker -> nonempty datasets -> child labels -> 601288 automatic daily chart without manual period -> desktop/mobile -> refresh -> normal entry -> empty minute period -> reopen\n",
  );
  console.log("Seven-day split download and 601288 actual daily chart: PASS");
} finally {
  await browser.close();
}
