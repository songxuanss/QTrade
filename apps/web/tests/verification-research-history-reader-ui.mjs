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
  const fixtures = JSON.parse(
    readFileSync(
      "../../.tools/verification-local/research-processing-fixtures.json",
    ),
  );
  for (const fixture of fixtures) {
    const name = fixture.kind === "financial_statement" ? "财务" : "公司行为";
    const panel = page.locator(
      fixture.kind === "financial_statement"
        ? "#financial-collection"
        : "#distribution-collection",
    );
    const search = panel.getByRole("searchbox", {
      name: `搜索${name}近期任务`,
      exact: true,
    });
    await search.fill(fixture.request_id);
    const row = panel.locator(`[data-research-job="${fixture.request_id}"]`);
    await expect(row).toBeVisible({ timeout: 60000 });
    await expect(
      panel.getByRole("button", { name: /重试当前任务|提前重试预算等待/ }),
    ).toHaveCount(0);
    await expect(
      panel.getByRole("button", { name: `预览${name}计划`, exact: true }),
    ).toBeDisabled();
    await row
      .getByRole("button", { name: "查看转换检查", exact: true })
      .click();
    await expect(row).toContainText("标准记录数量未知", { timeout: 30000 });
    const root = `http://127.0.0.1:3122/api/v1/workspaces/${scope}/data/collector/research`;
    assert.equal(
      (
        await page.request.post(`${root}/jobs/${fixture.request_id}/retry`, {
          headers: { "X-CSRF-Token": session.csrf_token },
          data: {},
        })
      ).status(),
      403,
    );
  }
  await page.reload({ timeout: 90000 });
  await expect(
    page.getByRole("searchbox", { name: "搜索财务近期任务", exact: true }),
  ).toHaveValue("");
  console.log(
    "PASS research history reader: shared-scope search/checks/reload, no mutation controls, retry403",
  );
} finally {
  await browser.close();
}
