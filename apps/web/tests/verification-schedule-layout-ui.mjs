import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
// Read-only visual QA of the expanded editor; it never saves or enables a plan.
const identity = JSON.parse(readFileSync("../../.tools/sso-local/state.json"));
const scope = JSON.parse(
  readFileSync("../../.tools/verification-local/budget-scope.json"),
).scope_id;
const browser = await chromium.launch();
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
  await expect(page).toHaveURL("http://127.0.0.1:13000/workspace/data", {
    timeout: 90000,
  });
  await page.getByLabel("切换工作区", { exact: true }).selectOption(scope);
  await page.getByRole("button", { name: "切换", exact: true }).click();
  await expect(page).toHaveURL("http://127.0.0.1:13000/", { timeout: 60000 });
  await page.goto("http://127.0.0.1:13000/workspace/data", { timeout: 90000 });
  await page.getByRole("tab", { name: "自动更新", exact: true }).click();
  const panel = page.getByRole("region", { name: "自动更新计划", exact: true });
  await panel.locator("summary").click();
  await expect(
    panel.getByLabel("自动计划运行时间", { exact: true }),
  ).toBeVisible();
  await expect(
    panel.getByRole("button", { name: "保存停用计划", exact: true }),
  ).toBeDisabled();
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    assert.equal(
      (await new AxeBuilder({ page }).include(".schedule-panel").analyze())
        .violations.length,
      0,
    );
    await panel.getByRole("heading", { name: "自动更新", exact: true }).click();
    await page.screenshot({
      path: `../../.tools/verification-local/review/schedule-editor-${width}.png`,
      fullPage: true,
    });
  }
  console.log(
    "PASS schedule layout: expanded desktop/mobile editor, axe0, no overflow, no mutations",
  );
} finally {
  await browser.close();
}
