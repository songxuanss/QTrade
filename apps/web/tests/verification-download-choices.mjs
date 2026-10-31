import assert from "node:assert/strict";
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const identity = JSON.parse(
  readFileSync("../../.tools/sso-local/state.json", "utf8"),
);
const origin = "http://127.0.0.1:3120";
const directory = "../../.tools/verification-local/review";
mkdirSync(directory, { recursive: true });
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
  await page.getByLabel("密码", { exact: true }).fill(identity.password);
  await page.getByRole("button", { name: "登 录", exact: true }).click();
  await expect(page).toHaveURL(/(?:3121\/auth\/consent|3120\/workspace\/data)/);
  if (new URL(page.url()).pathname === "/auth/consent") {
    await page.getByRole("button", { name: "同意授权", exact: true }).click();
  }
  await expect(page).toHaveURL(`${origin}/workspace/data`);
  async function submit(symbol, end) {
    await page.goto(`${origin}/workspace/data?data_view=ticks`);
    await page.locator(".tick-request-form [name=instrument_id]").fill(symbol);
    await page
      .locator(".tick-request-form [name=start]")
      .fill("2026-09-28T01:30");
    await page.locator(".tick-request-form [name=end]").fill(end);
    await page.getByRole("button", { name: "创建 Tick 下载任务" }).click();
    await page.waitForURL(/result=(?:success|complete|partial|error)/);
    await expect(page.locator("h1")).toHaveText("数据管理");
  }
  async function finishFirst() {
    await expect(page.locator(".data-notice[role=status]")).toContainText(
      "下载任务已提交",
    );
    const newest = page.locator("#collection .data-row").first();
    const id = await newest.getAttribute("data-task-id");
    assert.ok(id);
    const row = page.locator(`[data-task-id="${id}"]`);
    const deadline = Date.now() + 120_000;
    while (
      !(await row.locator(".data-status").innerText()).includes("已完成")
    ) {
      assert.ok(
        Date.now() < deadline,
        `Download timeout: ${await row.innerText()}`,
      );
      assert.notEqual(await row.locator(".data-status").innerText(), "失败");
      await page.waitForTimeout(1500);
      await page.getByRole("button", { name: "刷新数据", exact: true }).click();
      await expect(row).toBeVisible();
    }
    return id;
  }
  let symbol;
  for (const candidate of [
    "600036.SH",
    "600000.SH",
    "000001.SZ",
    "000002.SZ",
    "601398.SH",
    "601288.SH",
    "601988.SH",
    "601939.SH",
  ]) {
    await page.goto(`${origin}/workspace/data?data_view=ticks`);
    await submit(candidate, "2026-09-28T01:45");
    if (new URL(page.url()).searchParams.get("result") === "success") {
      symbol = candidate;
      break;
    }
  }
  assert.ok(
    symbol,
    "Need an unused symbol/window in the isolated verification database",
  );
  await finishFirst();
  const before = await page.locator("#collection .data-row").count();
  await submit(symbol, "2026-09-28T01:45");
  await expect(
    page.getByRole("heading", { name: "该时段已完整下载" }),
  ).toBeVisible();
  assert.equal(await page.locator("#collection .data-row").count(), before);
  await page.getByRole("link", { name: "取消", exact: true }).click();
  await submit(symbol, "2026-09-28T02:00");
  await expect(
    page.getByRole("heading", { name: "该时段已有下载记录" }),
  ).toBeVisible();
  assert.equal(await page.locator("#collection .data-row").count(), before);
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 844 });
    assert.deepEqual((await new AxeBuilder({ page }).analyze()).violations, []);
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    await page.screenshot({
      path: `${directory}/download-choice-${width}.png`,
      fullPage: true,
    });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page
    .getByRole("button", { name: "仅下载缺失数据", exact: true })
    .click();
  const missingId = await finishFirst();
  const missingRow = page.locator(`[data-task-id="${missingId}"]`);
  await expect(missingRow.locator(".data-period")).toContainText("09:45");
  await expect(missingRow.locator(".data-period")).toContainText("10:00");
  assert.equal(await page.locator("#collection .data-row").count(), before + 1);
  await submit(symbol, "2026-09-28T02:00");
  await expect(
    page.getByRole("heading", { name: "该时段已完整下载" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "重新下载全部", exact: true }).click();
  const allId = await finishFirst();
  await expect(
    page.locator(`[data-task-id="${allId}"] .data-period`),
  ).toContainText("09:30");
  assert.equal(await page.locator("#collection .data-row").count(), before + 2);
  await page.reload();
  await expect(page.locator(`[data-task-id="${allId}"]`)).toContainText(
    "已完成",
  );
  assert.deepEqual(errors, []);
  writeFileSync(
    `${directory}/download-choices-results.txt`,
    `Live source ${symbol}: duplicate notice with no mutation / partial choice / missing interval only / union full coverage / full redownload / persisted completion: PASS\n`,
  );
  console.log("Download choices with live provider and Worker: PASS");
} finally {
  await browser.close();
}
