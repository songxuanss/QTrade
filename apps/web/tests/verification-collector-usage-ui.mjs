import assert from "node:assert/strict";
import { readFileSync, writeFileSync, chmodSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
const browser = await chromium.launch();
const checks = [];
async function login(identity, viewport) {
  const context = await browser.newContext({ viewport });
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
  await page.waitForURL((u) => u.pathname === "/workspace/data", {
    timeout: 90000,
  });
  await page.getByRole("tab", { name: "来源与新鲜度", exact: true }).click();
  await page.getByRole("tab", { name: "用量与配额", exact: true }).click();
  const panel = page.getByRole("region", { name: "采集用量", exact: true });
  await expect(panel).toHaveAttribute("data-usage-scope", /[a-f0-9-]{36}/, {
    timeout: 60000,
  });
  const session = await (
    await page.request.get("http://127.0.0.1:3122/auth/session")
  ).json();
  return {
    context,
    page,
    panel,
    root: `http://127.0.0.1:3122/api/v1/workspaces/${session.workspace_id}/data/collector`,
    scope: session.workspace_id,
  };
}
try {
  const identity = JSON.parse(
    readFileSync("../../.tools/sso-local/state.json"),
  );
  const { context, page, panel, root, scope } = await login(
    { ...identity, email: "qtrade-integration@example.test" },
    { width: 1440, height: 1000 },
  );
  const initial = await page.request.get(root + "/usage");
  assert.equal(initial.status(), 200);
  const before = await initial.json();
  assert.equal(before.scope_id, scope);
  assert.equal(before.accounting, "internal_credit_safeguard");
  assert(before.request_count > 0 && before.recent_requests.length > 0);
  for (let i = 0; i < 3; i++) {
    const response = await page.request.get(root + "/usage");
    assert.equal(response.status(), 200);
    const value = await response.json();
    assert.equal(
      value.scope_budget.credits_used,
      before.scope_budget.credits_used,
    );
    assert.equal(value.request_count, before.request_count);
    assert.equal(value.denial_count, before.denial_count);
    assert(!JSON.stringify(value).includes("https://"));
    assert(!JSON.stringify(value).includes("token"));
  }
  checks.push("actual scoped persistence / repeated reads do not charge");
  await expect(panel).toContainText("与供应商账单及订阅权益独立");
  await expect(panel).toContainText("有效预留不代表连接仍在运行");
  await page.getByLabel("独立采集标的", { exact: true }).fill("601288.SH");
  const refresh = panel.getByRole("button", { name: "刷新用量", exact: true });
  await refresh.focus();
  await page.keyboard.press("Enter");
  await expect(panel).toContainText("用量已更新", { timeout: 30000 });
  await expect(refresh).toBeEnabled();
  await expect(page.getByLabel("独立采集标的", { exact: true })).toHaveValue(
    "601288.SH",
  );
  await panel
    .getByText("当前空间请求与拒绝明细（各最近 50 条）", { exact: true })
    .click();
  await expect(panel.getByRole("table")).toContainText(
    before.recent_requests[0].request_id,
  );
  checks.push("keyboard refresh / input retained / actual request details");
  const retained = await panel.getAttribute("data-usage-generated");
  await context.setOffline(true);
  await refresh.click();
  await expect(refresh).toBeEnabled({ timeout: 30000 });
  await expect(panel).toContainText("保留的快照不代表当前额度");
  assert.equal(await panel.getAttribute("data-usage-generated"), retained);
  await context.setOffline(false);
  await refresh.click();
  await expect(panel).toContainText("用量已更新", { timeout: 30000 });
  checks.push(
    "controlled browser disconnect / stale snapshot retained / recovery",
  );
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    const axe = await new AxeBuilder({ page })
      .include('[aria-label="采集用量"]')
      .analyze();
    assert.equal(
      axe.violations.length,
      0,
      JSON.stringify(
        axe.violations.map((v) => ({
          id: v.id,
          targets: v.nodes.map((n) => n.target),
        })),
      ),
    );
    const file = `../../.tools/verification-local/review/collector-usage-${width}.png`;
    await panel.screenshot({ path: file });
    chmodSync(file, 0o600);
    checks.push(`desktop/mobile ${width} / local scrolling / axe0`);
  }
  await page.reload({ timeout: 90000 });
  await expect(
    page.getByRole("region", { name: "采集用量", exact: true }),
  ).toHaveAttribute("data-usage-scope", scope, { timeout: 60000 });
  assert(
    [403, 404].includes(
      (
        await page.request.get(
          root.replace(scope, "00000000-0000-0000-0000-000000000000") +
            "/usage",
        )
      ).status(),
    ),
  );
  const anonymous = await browser.newContext();
  assert.equal((await anonymous.request.get(root + "/usage")).status(), 401);
  await anonymous.close();
  checks.push("refresh / cross-scope rejection / anonymous401");
  await context.close();
  const readonly = await login(
    JSON.parse(readFileSync("../../.tools/verification-local/approver.json")),
    { width: 390, height: 844 },
  );
  const readable = await readonly.page.request.get(readonly.root + "/usage");
  assert.equal(readable.status(), 200);
  assert.equal((await readable.json()).scope_id, readonly.scope);
  await readonly.context.close();
  checks.push("read-only member usage read on mobile");
  const file =
    "../../.tools/verification-local/review/collector-usage-ui-results.json";
  writeFileSync(
    file,
    JSON.stringify(
      {
        origin: "http://127.0.0.1:13000",
        integration: "actual SSO/API/scoped collector PostgreSQL",
        checks,
      },
      null,
      2,
    ),
  );
  chmodSync(file, 0o600);
  console.log(`PASS collector usage UI: ${checks.length} checks`);
} finally {
  await browser.close();
}
