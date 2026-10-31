import { expect, test } from "@playwright/test";

test("published datasets are selectable without a watchlist or typed code", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/workspace/market");
  await expect(page.getByRole("heading", { name: "市场终端" })).toBeVisible();
  const catalog = page.getByRole("region", { name: "已下载数据列表" });
  await expect(catalog.getByText("MOCK:ABC", { exact: true })).toBeVisible();
  const period = catalog.getByRole("link", { name: /1 分钟/ }).first();
  const href = await period.getAttribute("href");
  expect(href).toContain("instrument=MOCK%3AABC");
  expect(href).toContain("dataset_id=");
  await period.click();
  await expect(page.locator("#market-chart-heading")).toContainText("MOCK:ABC");
  await expect(page.locator(".market-chart svg")).toBeVisible();
  expect(
    await page.evaluate(() => document.body.scrollWidth <= innerWidth),
  ).toBe(true);
});
