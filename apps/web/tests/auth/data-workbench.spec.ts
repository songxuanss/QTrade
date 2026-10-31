import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("data workbench exposes readable scope and real job states", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.clock.setFixedTime(new Date("2026-01-02T00:00:00Z"));
  await page.goto("/workspace/data?data_view=collect");
  await expect(
    page.getByRole("heading", { name: "独立数据采集" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "配置采集范围" }),
  ).toBeVisible();
  await expect(page.getByText("尚未选择范围", { exact: false })).toBeVisible();
  await expect(page).toHaveScreenshot("data-collect-desktop.png", {
    fullPage: true,
    animations: "disabled",
  });

  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await expect(page).toHaveScreenshot("data-collect-mobile.png", {
    fullPage: true,
    animations: "disabled",
  });
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 1440, height: 1000 });

  await page.goto(
    "/workspace/data?data_view=collect&collect_view=results&collector_job=aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  );
  const parts = page.getByRole("region", { name: "采集任务分段" });
  await expect(parts.getByText("农业银行", { exact: false })).toBeVisible();
  await expect(parts.locator(".status-waiting")).toContainText("等待预算");
  await expect(parts.locator(".status-failed")).toContainText("失败");
  await expect(page).toHaveScreenshot("data-results-desktop.png", {
    fullPage: true,
    animations: "disabled",
  });
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);

  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.body.scrollWidth <= innerWidth),
  ).toBe(true);
  await expect(page).toHaveScreenshot("data-results-mobile.png", {
    fullPage: true,
    animations: "disabled",
  });
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("data refresh preserves drafts and rejected Tick values stay editable", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/workspace/data?data_view=ticks");
  const form = page.locator(".tick-request-form");
  const instrument = form.getByLabel("A 股代码（6 位）", { exact: true });
  const start = form.getByLabel("开始时间（UTC）", { exact: true });
  const end = form.getByLabel("结束时间（UTC）", { exact: true });
  const refresh = page.getByRole("button", { name: "刷新数据", exact: true });
  await expect(form).toBeVisible();
  await instrument.fill("601288.SH");
  await start.fill("2026-10-16T00:00");
  await end.fill("2026-10-15T00:00");

  const desktopRefresh = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      response.request().headers().rsc === "1" &&
      new URL(response.url()).pathname === "/workspace/data",
  );
  await refresh.focus();
  await page.keyboard.press("Enter");
  expect((await desktopRefresh).ok()).toBe(true);
  await expect(instrument).toHaveValue("601288.SH");
  await expect(start).toHaveValue("2026-10-16T00:00");
  await expect(end).toHaveValue("2026-10-15T00:00");

  await page.setViewportSize({ width: 390, height: 844 });
  await instrument.fill("600519.SH");
  const mobileRefresh = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      response.request().headers().rsc === "1" &&
      new URL(response.url()).pathname === "/workspace/data",
  );
  await refresh.focus();
  await page.keyboard.press("Space");
  expect((await mobileRefresh).ok()).toBe(true);
  await expect(instrument).toHaveValue("600519.SH");
  await expect(start).toHaveValue("2026-10-16T00:00");
  await expect(end).toHaveValue("2026-10-15T00:00");

  const rejection = new URLSearchParams({
    result: "invalid",
    data_view: "ticks",
    download_instrument: "600519.SH",
    download_start: "2026-10-16T00:00",
    download_end: "2026-10-15T00:00",
  });
  await page.goto(`/workspace/data?${rejection}`);
  await expect(
    page.locator(".data-workbench").getByRole("alert"),
  ).toContainText("下载参数不符合要求");
  await expect(instrument).toHaveValue("600519.SH");
  await expect(start).toHaveValue("2026-10-16T00:00");
  await expect(end).toHaveValue("2026-10-15T00:00");
  await instrument.focus();
  await page.keyboard.press("Control+A");
  await page.keyboard.type("601288.SH");
  await expect(instrument).toHaveValue("601288.SH");
  expect(
    await page.evaluate(() => document.body.scrollWidth <= innerWidth),
  ).toBe(true);
});
