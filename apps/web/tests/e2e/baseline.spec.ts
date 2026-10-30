import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("public entry renders safely and exposes a healthy process", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await expect(page).toHaveTitle("QTrade");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("工作台");
  await expect(page.getByText("实盘已关闭")).toBeVisible();
  const response = await request.get("/health");
  expect(response.status()).toBe(200);
  expect(await response.json()).toEqual({
    status: "ok",
    service: "qtrade-web",
  });
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("unknown resource returns 404", async ({ request }) => {
  for (const path of ["/nonexistent-resource", "/workspace/unknown"]) {
    expect((await request.get(path)).status()).toBe(404);
  }
});

test("desktop visual regression", async ({ page }) => {
  await page.goto("/");
  expect(
    await page
      .locator(".identity")
      .evaluate(
        (element) => element.getBoundingClientRect().bottom <= innerHeight,
      ),
  ).toBe(true);
  await expect(page).toHaveScreenshot("workbench-desktop.png", {
    fullPage: true,
  });
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "跳转到主内容" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("main")).toBeFocused();
});
test("mobile navigation and visual regression", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await expect(page).toHaveScreenshot("workbench-mobile.png", {
    fullPage: true,
  });
  await page.getByRole("button", { name: "打开主导航" }).click();
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("button", { name: "打开主导航" }),
  ).toHaveAttribute("aria-expanded", "false");
  await page.getByRole("button", { name: "打开主导航" }).click();
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "策略研究" })
    .click();
  await expect(page).toHaveURL(/\/workspace\/strategies$/);
  await expect(page.getByRole("status")).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});
test("UI states are accessible and visually stable", async ({ page }) => {
  await page.goto("/design-system");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await expect(page).toHaveScreenshot("ui-states.png", { fullPage: true });
});

test("missing page keeps the workbench design and accessible navigation", async ({
  page,
}) => {
  const response = await page.goto("/nonexistent-resource");
  expect(response?.status()).toBe(404);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "页面不存在",
  );
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await expect(page).toHaveScreenshot("not-found.png", { fullPage: true });
  await page.getByRole("link", { name: "返回工作台", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("工作台");
});
