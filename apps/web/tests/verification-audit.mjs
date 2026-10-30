import assert from "node:assert/strict";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const state = JSON.parse(readFileSync("../../.tools/sso-local/state.json"));
const approver = JSON.parse(
  readFileSync("../../.tools/verification-local/approver.json"),
);
const origin = "http://127.0.0.1:3120";
const api = "http://127.0.0.1:3122";
const directory = "../../.tools/verification-local/review";
mkdirSync(directory, { recursive: true });
const browser = await chromium.launch();
try {
  async function login(email, password) {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
    });
    const page = await context.newPage();
    await page.goto(`${origin}/workspace/audit`);
    await page.getByRole("textbox", { name: "邮箱", exact: true }).fill(email);
    await page.getByLabel("密码", { exact: true }).fill(password);
    await page.getByRole("button", { name: "登 录", exact: true }).click();
    await page.waitForURL(/3120\/workspace\/audit|3121\/auth\/consent/);
    if (page.url().includes("/auth/consent")) {
      await page.getByRole("button", { name: "同意授权", exact: true }).click();
    }
    await expect(page).toHaveURL(`${origin}/workspace/audit`);
    await expect(
      page.getByRole("heading", { name: "审计记录", exact: true }),
    ).toBeVisible();
    const sessionResponse = await page.request.get(`${api}/auth/session`);
    assert.equal(sessionResponse.status(), 200);
    return { page, session: await sessionResponse.json() };
  }
  const operator = await login(
    "qtrade-integration@example.test",
    state.password,
  );

  const root = `${api}/api/v1/workspaces/${operator.session.workspace_id}`;
  const headers = { "X-CSRF-Token": operator.session.csrf_token };
  const all = await (await operator.page.request.get(`${root}/audit`)).json();
  assert.ok(all.events.length > 0);
  const tracedExport = await operator.page.request.get(`${root}/audit/export`);
  assert.equal(tracedExport.status(), 200);
  const traceId = tracedExport.headers()["x-qtrade-trace-id"];
  assert.match(traceId, /^[0-9a-f-]{36}$/);
  const correlated = await (
    await operator.page.request.get(`${root}/audit?trace_id=${traceId}`)
  ).json();
  assert.ok(correlated.events.some((item) => item.action === "audit.export"));
  const event = all.events[0];
  await operator.page
    .getByLabel("审计操作", { exact: true })
    .fill(event.action);
  await operator.page
    .getByLabel("审计追踪 ID", { exact: true })
    .fill(event.trace_id);
  await operator.page
    .getByRole("button", { name: "查询审计", exact: true })
    .click();
  await expect(
    operator.page.locator(".audit-table tbody tr").first(),
  ).toContainText(event.action);
  await operator.page.reload();
  await expect(
    operator.page.locator(".audit-table tbody tr").first(),
  ).toContainText(event.trace_id);
  const downloading = operator.page.waitForEvent("download");
  await operator.page
    .getByRole("link", { name: "导出 CSV", exact: true })
    .click();
  const download = await downloading;
  const csv = readFileSync(await download.path(), "utf8");
  assert.ok(csv.includes(event.action) && csv.includes(event.trace_id));
  assert.ok(
    !csv.includes("session_digest") && !csv.includes("token_ciphertext"),
  );
  assert.ok(
    !csv.includes(state.password) && !csv.includes(state.client_secret),
  );
  for (const viewport of [
    { width: 1440, height: 1000 },
    { width: 390, height: 844 },
  ]) {
    await operator.page.setViewportSize(viewport);
    await expect(operator.page.locator("h1")).toBeVisible();
    assert.deepEqual(
      (await new AxeBuilder({ page: operator.page }).analyze()).violations,
      [],
    );
    assert.ok(
      await operator.page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    await operator.page.screenshot({
      path: `${directory}/audit-${viewport.width}.png`,
      fullPage: true,
    });
  }
  await operator.page
    .getByLabel("审计操作者 ID", { exact: true })
    .fill("invalid-id");
  await operator.page
    .getByRole("button", { name: "查询审计", exact: true })
    .click();
  await expect(
    operator.page.locator(".audit-page .data-notice[role=alert]"),
  ).toContainText("筛选条件无效");
  const deniedRange = await operator.page.request.get(
    `${root}/audit/export?start=2025-01-01T00:00:00Z&end=2026-01-01T00:00:00Z`,
  );
  assert.equal(deniedRange.status(), 400);
  const reviewer = await login(approver.email, approver.password);
  assert.equal(
    (
      await operator.page.request.put(
        `${root}/members/${reviewer.session.user_id}`,
        { headers, data: { role: "viewer", active: true } },
      )
    ).status(),
    200,
  );
  try {
    await reviewer.page.reload();
    await reviewer.page
      .getByLabel("切换工作区", { exact: true })
      .selectOption(operator.session.workspace_id);
    await reviewer.page
      .getByRole("button", { name: "切换", exact: true })
      .click();
    await expect(reviewer.page).toHaveURL(`${origin}/`);
    await reviewer.page.goto(`${origin}/workspace/audit`);
    await expect(
      reviewer.page.getByRole("link", { name: "导出 CSV", exact: true }),
    ).toHaveCount(0);
    await expect(reviewer.page.locator(".permission-state")).toContainText(
      "只有工作区管理员与风险管理员",
    );
    assert.equal(
      (await reviewer.page.request.get(`${root}/audit/export`)).status(),
      403,
    );
    assert.equal(
      (await reviewer.page.request.get(`${root}/audit`)).status(),
      403,
    );
    assert.equal(
      (
        await reviewer.page.request.get(
          `${api}/api/v1/workspaces/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/audit/export`,
        )
      ).status(),
      404,
    );
    const denied = await (
      await operator.page.request.get(
        `${root}/audit?action=permission.denied&result=denied&actor_id=${reviewer.session.user_id}`,
      )
    ).json();
    assert.ok(denied.events.length >= 2);
  } finally {
    assert.equal(
      (
        await operator.page.request.put(
          `${root}/members/${reviewer.session.user_id}`,
          { headers, data: { role: "risk_manager", active: true } },
        )
      ).status(),
      200,
    );
  }
  writeFileSync(
    `${directory}/audit-results.txt`,
    "PASS: real SSO / stored audit filters / refresh / browser CSV export / masked metadata / invalid filters / viewer and tenant export denial recorded / desktop-mobile accessibility\n",
  );
  console.log(
    "Actual audit UI / PostgreSQL / scoped filters and CSV / role denial / refresh / desktop-mobile: PASS",
  );
} finally {
  await browser.close();
}
