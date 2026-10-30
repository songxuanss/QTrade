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
    await page.goto(`${origin}/workspace/risk`);
    await page.getByRole("textbox", { name: "邮箱", exact: true }).fill(email);
    await page.getByLabel("密码", { exact: true }).fill(password);
    await page.getByRole("button", { name: "登 录", exact: true }).click();
    await page.waitForURL(/3120\/workspace\/risk|3121\/auth\/consent/);
    if (page.url().includes("/auth/consent")) {
      await page.getByRole("button", { name: "同意授权", exact: true }).click();
    }
    await expect(page).toHaveURL(`${origin}/workspace/risk`);
    await expect(
      page.getByRole("heading", { name: "风控中心", exact: true }),
    ).toBeVisible();
    const sessionResponse = await page.request.get(`${api}/auth/session`);
    assert.equal(sessionResponse.status(), 200);
    return { page, session: await sessionResponse.json() };
  }
  const operator = await login(
    "qtrade-integration@example.test",
    state.password,
  );
  const reviewer = await login(approver.email, approver.password);
  const root = `${api}/api/v1/workspaces/${operator.session.workspace_id}`;
  const headers = { "X-CSRF-Token": operator.session.csrf_token };
  const grant = await operator.page.request.put(
    `${root}/members/${reviewer.session.user_id}`,
    { headers, data: { role: "risk_manager", active: true } },
  );
  assert.equal(grant.status(), 200);
  await reviewer.page.reload();
  await reviewer.page
    .getByLabel("切换工作区", { exact: true })
    .selectOption(operator.session.workspace_id);
  await reviewer.page
    .getByRole("button", { name: "切换", exact: true })
    .click();
  await expect(reviewer.page).toHaveURL(`${origin}/`);
  await expect(reviewer.page.locator(".workspace-switch")).toContainText(
    "风险管理员",
  );
  await reviewer.page.goto(`${origin}/workspace/risk`);
  const reviewerSession = await (
    await reviewer.page.request.get(`${api}/auth/session`)
  ).json();
  assert.equal(reviewerSession.workspace_id, operator.session.workspace_id);
  const reviewerHeaders = { "X-CSRF-Token": reviewerSession.csrf_token };
  const current = await (
    await operator.page.request.get(`${root}/kill-switch`)
  ).json();
  assert.equal(
    current.active,
    false,
    "Use an isolated environment with no existing active switch",
  );
  await operator.page
    .getByLabel("停止原因", { exact: true })
    .fill("端到端验收：检查工作区停止与独立恢复审批");
  await operator.page
    .getByLabel("输入“停止交易”确认", { exact: true })
    .fill("STOP");
  await operator.page.getByRole("button", { name: "触发交易安全开关" }).click();
  assert.equal(
    (await (await operator.page.request.get(`${root}/kill-switch`)).json())
      .active,
    false,
  );
  await operator.page
    .getByLabel("输入“停止交易”确认", { exact: true })
    .fill("停止交易");
  await operator.page.getByRole("button", { name: "触发交易安全开关" }).click();
  await expect(
    operator.page.getByText("已停止交易", { exact: true }),
  ).toBeVisible();
  await operator.page.reload();
  await expect(
    operator.page.getByText("已停止交易", { exact: true }),
  ).toBeVisible();
  await operator.page.getByRole("button", { name: "申请恢复交易" }).click();
  await expect(
    operator.page.getByText("你是申请人，请由另一位授权成员审批。"),
  ).toBeVisible();
  const pending = await (
    await operator.page.request.get(`${root}/kill-switch/recovery`)
  ).json();
  assert.equal(pending.length, 1);
  assert.equal(pending[0].self_requested, true);
  const selfApprove = await operator.page.request.post(
    `${root}/kill-switch/recovery/${pending[0].request_id}/approve`,
    { headers, data: { reason: "申请人不能自行审批" } },
  );
  assert.equal(selfApprove.status(), 403);
  assert.equal(
    (await (await operator.page.request.get(`${root}/kill-switch`)).json())
      .active,
    true,
  );
  await reviewer.page.reload();
  await reviewer.page
    .getByLabel("审批原因", { exact: true })
    .fill("另一位授权成员完成检查，同意恢复");
  await reviewer.page
    .getByLabel("输入“批准恢复”确认", { exact: true })
    .fill("批准恢复");
  await reviewer.page.getByRole("button", { name: "批准恢复交易" }).click();
  await expect(
    reviewer.page.getByText("未触发", { exact: true }),
  ).toBeVisible();
  await operator.page.reload();
  await expect(
    operator.page.getByText("未触发", { exact: true }),
  ).toBeVisible();
  assert.equal(
    (
      await (
        await operator.page.request.get(`${root}/kill-switch/recovery`)
      ).json()
    ).length,
    0,
  );
  const deniedWorkspace = await reviewer.page.request.post(
    `${api}/auth/session/workspace`,
    {
      headers: reviewerHeaders,
      data: { workspace_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" },
    },
  );
  assert.equal(deniedWorkspace.status(), 403);
  const demote = await operator.page.request.put(
    `${root}/members/${reviewer.session.user_id}`,
    { headers, data: { role: "viewer", active: true } },
  );
  assert.equal(demote.status(), 200);
  await reviewer.page.reload();
  await expect(
    reviewer.page.getByText("仅管理员和风控管理员可操作交易安全开关。"),
  ).toBeVisible();
  assert.equal(
    await reviewer.page
      .getByRole("button", { name: "触发交易安全开关" })
      .count(),
    0,
  );
  const denied = await reviewer.page.request.post(
    `${root}/kill-switch/activate`,
    { headers: reviewerHeaders, data: { reason: "只读成员不能触发" } },
  );
  assert.equal(denied.status(), 403);
  const restoreRole = await operator.page.request.put(
    `${root}/members/${reviewer.session.user_id}`,
    { headers, data: { role: "risk_manager", active: true } },
  );
  assert.equal(restoreRole.status(), 200);
  for (const width of [1440, 390]) {
    await operator.page.setViewportSize({ width, height: 844 });
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
      path: `${directory}/risk-verified-${width}.png`,
      fullPage: true,
    });
  }
  writeFileSync(
    `${directory}/risk-results.txt`,
    "Real SSO two identities / live workspace role grant and session switch / typed confirmation / activation persistence / self approval rejection / independent approval / read-only UI and API rejection / desktop-mobile accessibility: PASS\n",
  );
  console.log(
    "Risk controls with real independent identities and persistence: PASS",
  );
} finally {
  await browser.close();
}
