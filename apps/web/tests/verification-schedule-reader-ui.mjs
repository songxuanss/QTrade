import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
const identity = JSON.parse(
  readFileSync("../../.tools/verification-local/approver.json"),
);
const scope = JSON.parse(
  readFileSync("../../.tools/verification-local/budget-scope.json"),
).scope_id;
const browser = await chromium.launch();
try {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
  });
  const page = await context.newPage();
  await page.goto("http://127.0.0.1:13000/workspace/data", { timeout: 90000 });
  await page
    .getByRole("textbox", { name: "邮箱", exact: true })
    .fill(identity.email);
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
  await page.getByRole("button", { name: "打开主导航", exact: true }).click();
  await page.getByLabel("切换工作区", { exact: true }).selectOption(scope);
  await page.getByRole("button", { name: "切换", exact: true }).click();
  await expect(page).toHaveURL("http://127.0.0.1:13000/", { timeout: 60000 });
  await page.goto("http://127.0.0.1:13000/workspace/data", { timeout: 90000 });
  const session = await (
    await page.request.get("http://127.0.0.1:3122/auth/session")
  ).json();
  assert.equal(session.workspace_id, scope);
  const root = `http://127.0.0.1:3122/api/v1/workspaces/${scope}/data/collector/schedules`;
  const response = await page.request.get(root);
  assert.equal(response.status(), 200);
  const records = await response.json();
  assert(records.length > 0);
  const panel = page.getByRole("region", { name: "自动更新计划", exact: true });
  await expect(panel.locator(".schedule-card")).toHaveCount(records.length, {
    timeout: 60000,
  });
  await expect(panel.locator("summary")).toHaveCount(0);
  await expect(
    panel.getByRole("button", { name: /启用自动计划|停用自动计划/ }),
  ).toHaveCount(0);
  assert.equal(
    (
      await page.request.post(
        `${root}/${records[0].draft.schedule_id}/enable`,
        {
          headers: { "X-CSRF-Token": session.csrf_token },
          data: { revision: records[0].revision },
        },
      )
    ).status(),
    403,
  );
  await page.reload({ timeout: 90000 });
  await expect(panel).toBeVisible();
  console.log(
    "PASS schedule reader UI: shared-scope read/reload, no write controls, mutation403",
  );
} finally {
  await browser.close();
}
