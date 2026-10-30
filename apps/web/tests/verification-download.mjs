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
  await expect(page.getByText("下载时长不限", { exact: true })).toBeVisible();
  await page
    .locator(".tick-request-form [name=instrument_id]")
    .fill("600519.SH");
  await page
    .locator(".tick-request-form [name=start]")
    .fill("2026-09-29T01:30");
  await page.locator(".tick-request-form [name=end]").fill("2026-09-28T07:00");
  await page.getByRole("button", { name: "创建 Tick 下载任务" }).click();
  await expect(page.locator(".data-notice[role=alert]")).toContainText(
    "下载参数不符合要求",
  );
  await page
    .locator(".tick-request-form [name=instrument_id]")
    .fill("600519.SH");
  await page
    .locator(".tick-request-form [name=start]")
    .fill("2026-09-28T01:30");
  await page.locator(".tick-request-form [name=end]").fill("2026-09-28T07:00");
  await page.getByRole("button", { name: "创建 Tick 下载任务" }).click();
  await page.waitForURL(/result=(?:complete|partial|success|error)/);
  if (new URL(page.url()).searchParams.get("result") === "complete") {
    await expect(
      page.getByRole("heading", { name: "该时段已完整下载" }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "重新下载全部", exact: true })
      .click();
  } else if (new URL(page.url()).searchParams.get("result") === "partial") {
    await page
      .getByRole("button", { name: "仅下载缺失数据", exact: true })
      .click();
  }
  await expect(page.locator(".data-notice[role=status]")).toContainText(
    "操作已完成",
  );
  const row = page.locator("#collection .data-row").first();
  await expect(row).toContainText("600519.SH");
  const deadline = Date.now() + 120_000;
  while (!(await row.innerText()).includes("已完成")) {
    assert.ok(
      Date.now() < deadline,
      `Download did not finish: ${await row.innerText()}`,
    );
    assert.ok(
      !(await row.locator(".data-status").innerText()).includes("失败"),
      `Download failed: ${await row.innerText()}`,
    );
    await page.waitForTimeout(1500);
    await page.getByRole("link", { name: "刷新数据", exact: true }).click();
    await expect(row).toBeVisible();
  }
  await expect(row).toContainText(/[1-9]\d* 批次/);
  await expect(page.locator("#batches .data-row").first()).toContainText(
    /[1-9][\d,]* 条记录/,
  );
  await expect(page.locator("#batches .data-hash").first()).toContainText(
    "SHA-256",
  );
  await page.reload();
  await expect(row).toContainText("已完成");
  await expect(page.locator("#batches .data-row").first()).toBeVisible();
  for (const viewport of [
    { width: 1440, height: 1000 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(viewport);
    assert.deepEqual((await new AxeBuilder({ page }).analyze()).violations, []);
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    await page.screenshot({
      path: `${directory}/data-${viewport.width}.png`,
      fullPage: true,
    });
  }
  assert.deepEqual(errors, []);
  writeFileSync(
    `${directory}/download-results.txt`,
    "Real SSO / UI invalid range rejection / task submission / live MaiRui / Worker / persisted batches / refresh / desktop-mobile accessibility: PASS\n",
  );
  console.log("Live A-share download end-to-end: PASS");
} finally {
  await browser.close();
}
