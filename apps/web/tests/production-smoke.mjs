import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { chromium } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const origin = process.env.QTRADE_TEST_WEB_ORIGIN ?? "http://127.0.0.1:13000";
const browser = await chromium.launch();
try {
  const context = await browser.newContext({
    viewport: { width: 1280, height: 900 },
  });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const response = await page.goto(origin);
  assert.equal(response.status(), 200);
  assert.equal(await page.title(), "QTrade");
  assert.equal(await page.locator("h1").textContent(), "工作台");
  assert.deepEqual((await new AxeBuilder({ page }).analyze()).violations, []);
  for (const path of [
    "/design-system",
    "/workspace/unknown",
    "/nonexistent-resource",
  ]) {
    const missing = await page.request.get(new URL(path, origin).href);
    assert.equal(missing.status(), 404, `${path} must reject before streaming`);
    assert.equal((await missing.text()).includes("界面状态"), false);
  }
  await mkdir("test-results", { recursive: true });
  await page.screenshot({
    path: "test-results/production-workbench.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    true,
  );
  assert.deepEqual((await new AxeBuilder({ page }).analyze()).violations, []);
  assert.deepEqual(errors, []);
  await context.close();
  console.log(
    "Production UI, desktop/mobile accessibility, hard 404 and preview isolation: PASS",
  );
} finally {
  await browser.close();
}
