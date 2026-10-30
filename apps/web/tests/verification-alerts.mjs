import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
const state = JSON.parse(readFileSync("../../.tools/sso-local/state.json"));
const origin = "http://127.0.0.1:3120";
const api = "http://127.0.0.1:3122";
const directory = "../../.tools/verification-local/review";
const name = `UI告警验收 ${Date.now()}`;
const browser = await chromium.launch();
try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${origin}/workspace/alerts`);
  await page
    .getByRole("textbox", { name: "邮箱", exact: true })
    .fill("qtrade-integration@example.test");
  await page.getByLabel("密码", { exact: true }).fill(state.password);
  await page.getByRole("button", { name: "登 录", exact: true }).click();
  await page.waitForURL(/3120\/workspace\/alerts|3121\/auth\/consent/);
  if (page.url().includes("/auth/consent"))
    await page.getByRole("button", { name: "同意授权", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "告警中心", exact: true }),
  ).toBeVisible();
  const session = await (await page.request.get(`${api}/auth/session`)).json();
  const root = `${api}/api/v1/workspaces/${session.workspace_id}`;
  await page.getByText("创建告警规则", { exact: true }).click();
  await page.getByLabel("规则名称", { exact: true }).fill(name);
  await page.getByLabel("触发条件", { exact: true }).fill("price >");
  await page.getByLabel("冷却时间（秒）", { exact: true }).fill("0");
  await page.getByRole("button", { name: "保存告警规则", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("输入不符合要求");
  assert(
    !(await (await page.request.get(`${root}/alert-rules`)).json()).some(
      (rule) => rule.name === name,
    ),
  );
  await page.getByText("创建告警规则", { exact: true }).click();
  await page.getByLabel("规则名称", { exact: true }).fill(name);
  await page.getByLabel("触发条件", { exact: true }).fill("price > 100");
  await page.getByLabel("冷却时间（秒）", { exact: true }).fill("0");
  await page.getByRole("button", { name: "保存告警规则", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("规则已保存");
  await page.reload();
  await expect(page.getByRole("row").filter({ hasText: name })).toContainText(
    "v1",
  );
  await page
    .getByRole("button", { name: `停用规则 ${name}`, exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: `启用规则 ${name}`, exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: name })).toContainText(
    "v2",
  );
  await page
    .getByRole("button", { name: `启用规则 ${name}`, exact: true })
    .click();
  await expect(page.getByRole("row").filter({ hasText: name })).toContainText(
    "v3",
  );
  await page.getByText("手动评估规则", { exact: true }).click();
  await page
    .getByLabel("事件事实（JSON）", { exact: true })
    .fill('{"price":101}');
  await page.getByRole("button", { name: "提交规则评估", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("评估已提交");
  let event;
  for (let attempt = 0; attempt < 60; attempt++) {
    await page.reload();
    const link = page.locator(".alert-row").filter({ hasText: name });
    if (await link.count()) {
      await link.first().click();
      break;
    }
    await page.waitForTimeout(500);
  }
  await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
  event = (
    await (await page.request.get(`${root}/alert-rules/events`)).json()
  ).find((item) => item.title === name);
  assert(event && event.status === "open");
  let handled = 0;
  for (const action of ["认领", "确认告警", "静默"]) {
    await page.getByRole("button", { name: action, exact: true }).click();
    await expect(page.getByRole("status")).toContainText("处置已记录");
    await expect(page.locator(".alert-history li")).toHaveCount(++handled);
  }
  await page
    .getByLabel("处置备注", { exact: true })
    .fill("通过真实浏览器完成排查并核对刷新后的处置历史");
  await page.getByRole("button", { name: "添加备注", exact: true }).click();
  await expect(page.locator(".alert-history")).toContainText(
    "通过真实浏览器完成排查",
  );
  await page.getByRole("button", { name: "关闭告警", exact: true }).click();
  await expect(page.locator(".alert-facts")).toContainText("已关闭");
  await page.reload();
  await expect(page.locator(".alert-history li")).toHaveCount(5);
  assert.equal(
    (
      await (
        await page.request.get(`${root}/alert-rules/events/${event.event_id}`)
      ).json()
    ).status,
    "closed",
  );
  await page
    .getByRole("button", { name: `停用规则 ${name}`, exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: `启用规则 ${name}`, exact: true }),
  ).toBeVisible();
  const approver = JSON.parse(
    readFileSync("../../.tools/verification-local/approver.json"),
  );
  const viewerContext = await browser.newContext();
  const viewer = await viewerContext.newPage();
  await viewer.goto(`${origin}/workspace/alerts`);
  await viewer
    .getByRole("textbox", { name: "邮箱", exact: true })
    .fill(approver.email);
  await viewer.getByLabel("密码", { exact: true }).fill(approver.password);
  await viewer.getByRole("button", { name: "登 录", exact: true }).click();
  await viewer.waitForURL(/3120\/workspace\/alerts|3121\/auth\/consent/);
  if (viewer.url().includes("/auth/consent"))
    await viewer.getByRole("button", { name: "同意授权", exact: true }).click();
  await expect(
    viewer.getByRole("heading", { name: "告警中心", exact: true }),
  ).toBeVisible();
  const viewerSession = await (
    await viewer.request.get(`${api}/auth/session`)
  ).json();
  const memberPath = `${root}/members/${viewerSession.user_id}`;
  const grantHeaders = { "X-CSRF-Token": session.csrf_token };
  assert.equal(
    (
      await page.request.put(memberPath, {
        headers: grantHeaders,
        data: { role: "viewer", active: true },
      })
    ).status(),
    200,
  );
  try {
    await viewer.reload();
    await viewer
      .getByLabel("切换工作区", { exact: true })
      .selectOption(session.workspace_id);
    await viewer.getByRole("button", { name: "切换", exact: true }).click();
    await expect(viewer).toHaveURL(`${origin}/`);
    await viewer.goto(`${origin}/workspace/alerts?event_id=${event.event_id}`);
    await expect(
      viewer.getByRole("heading", { name: "告警中心", exact: true }),
    ).toBeVisible();
    await expect(viewer.getByText("创建告警规则", { exact: true })).toHaveCount(
      0,
    );
    await expect(viewer.getByText("手动评估规则", { exact: true })).toHaveCount(
      0,
    );
    await expect(
      viewer.getByRole("button", { name: `启用规则 ${name}`, exact: true }),
    ).toHaveCount(0);
    await expect(
      viewer.getByRole("button", { name: "认领", exact: true }),
    ).toHaveCount(0);
    const currentViewerSession = await (
      await viewer.request.get(`${api}/auth/session`)
    ).json();
    const readonlyHeaders = { "X-CSRF-Token": currentViewerSession.csrf_token };
    assert.equal(
      (
        await viewer.request.patch(
          `${root}/alert-rules/${event.context_snapshot.rule.rule_id}/enabled`,
          { headers: readonlyHeaders, data: { enabled: true } },
        )
      ).status(),
      403,
    );
    assert.equal(
      (
        await viewer.request.post(`${root}/alert-evaluations`, {
          headers: readonlyHeaders,
          data: { source: "market", facts: { price: 101 } },
        })
      ).status(),
      403,
    );
    assert.equal(
      (
        await viewer.request.post(
          `${root}/alert-rules/events/${event.event_id}/actions/note`,
          { headers: readonlyHeaders, data: { note: "denied" } },
        )
      ).status(),
      403,
    );
  } finally {
    assert.equal(
      (
        await page.request.put(memberPath, {
          headers: grantHeaders,
          data: { role: "risk_manager", active: true },
        })
      ).status(),
      200,
    );
    await viewerContext.close();
  }
  for (const [label, width, height] of [
    ["desktop", 1440, 1000],
    ["mobile", 390, 844],
  ]) {
    await page.setViewportSize({ width, height });
    assert.deepEqual((await new AxeBuilder({ page }).analyze()).violations, []);
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    await page.screenshot({
      path: `${directory}/alerts-${label}.png`,
      fullPage: true,
    });
  }
  assert.deepEqual(errors, []);
  writeFileSync(
    `${directory}/alerts-results.txt`,
    `PASS: UI rule validation/create/toggle, Worker evaluation, UI claim/acknowledge/silence/note/close, persistence, desktop/mobile axe.\nRule: ${name}\nEvent: ${event.event_id}\nWorkspace: ${session.workspace_id}\n`,
  );
  console.log("Actual UI alert rule and Worker disposition workflow: PASS");
} finally {
  await browser.close();
}
