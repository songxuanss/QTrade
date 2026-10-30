import assert from "node:assert/strict";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { chromium, expect } from "@playwright/test";

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
    await page.goto(`${origin}/workspace/data`);
    await page.getByRole("textbox", { name: "邮箱", exact: true }).fill(email);
    await page.getByLabel("密码", { exact: true }).fill(password);
    await page.getByRole("button", { name: "登 录", exact: true }).click();
    await page.waitForURL(/3120\/workspace\/data|3121\/auth\/consent/);
    if (page.url().includes("/auth/consent")) {
      await page.getByRole("button", { name: "同意授权", exact: true }).click();
    }
    await expect(page).toHaveURL(`${origin}/workspace/data`);
    await expect(
      page.getByRole("heading", { name: "数据管理", exact: true }),
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
  await reviewer.page.goto(`${origin}/workspace/data`);
  const reviewerSession = await (
    await reviewer.page.request.get(`${api}/auth/session`)
  ).json();
  assert.equal(reviewerSession.workspace_id, operator.session.workspace_id);
  const reviewerHeaders = { "X-CSRF-Token": reviewerSession.csrf_token };

  await reviewer.page.goto(`${origin}/workspace/data`);
  await expect(reviewer.page.locator(".history-request-form")).toHaveCount(0);
  await expect(
    reviewer.page.getByRole("button", { name: "预览并保存成员", exact: true }),
  ).toHaveCount(0);
  await expect(
    reviewer.page.getByRole("button", {
      name: "保存自定义股票池",
      exact: true,
    }),
  ).toHaveCount(0);
  {
    const response = await reviewer.page.request.get(
      `${root}/data/universes/definitions?limit=1`,
    );
    assert.ok([200, 503].includes(response.status()));
    if (response.status() === 503)
      console.log(
        "Live source catalog unavailable (503); not an integration pass. Persisted campaign read and mutation restrictions checked separately.",
      );
  }
  assert.equal(
    (
      await reviewer.page.request.post(`${root}/data/universes/resolve`, {
        headers: reviewerHeaders,
        data: { universe_id: "mairui:hs300" },
      })
    ).status(),
    403,
  );
  assert.equal(
    (
      await reviewer.page.request.post(`${root}/data/universes/custom`, {
        headers: reviewerHeaders,
        data: { name: "denied", instrument_ids: ["601288.SH"] },
      })
    ).status(),
    403,
  );

  await expect(
    reviewer.page.getByRole("button", {
      name: "预览全部成员下载任务",
      exact: true,
    }),
  ).toHaveCount(0);
  assert.equal(
    (await reviewer.page.request.get(`${root}/data/campaigns`)).status(),
    200,
  );
  assert.equal(
    (
      await reviewer.page.request.post(`${root}/data/campaigns/preview`, {
        headers: reviewerHeaders,
        data: {
          snapshot_ids: ["00000000-0000-4000-8000-000000000001"],
          start: "2025-09-01T00:00:00Z",
          end: "2025-09-02T00:00:00Z",
        },
      })
    ).status(),
    403,
  );
  const body = {
    source: "mairui.a-share.bar",
    authorization_id: "mairui-default",
    instrument_id: "600519.SH",
    granularity: "1d",
    start: "2025-08-31T16:00:00Z",
    end: "2025-09-05T16:00:00Z",
  };
  const before = await (
    await operator.page.request.get(`${root}/data/tasks`)
  ).json();
  assert.equal(
    (
      await reviewer.page.request.post(`${root}/data/tasks`, {
        headers: reviewerHeaders,
        data: body,
      })
    ).status(),
    403,
  );
  assert.equal(
    (
      await operator.page.request.post(`${root}/data/tasks`, {
        headers,
        data: { ...body, granularity: "1m" },
      })
    ).status(),
    503,
  );
  const options = await (
    await operator.page.request.get(`${root}/data/source-options`)
  ).json();
  assert.ok(
    !options
      .find((item) => item.source === "mairui.a-share.bar")
      .granularities.includes("1m"),
  );
  const after = await (
    await operator.page.request.get(`${root}/data/tasks`)
  ).json();
  assert.equal(after.length, before.length);
  const saved = JSON.parse(
    readFileSync(`${directory}/history-1d-results.json`),
  );
  assert.equal(
    (
      await reviewer.page.request.get(
        `${root}/data/datasets/${saved.datasetId}/quality`,
      )
    ).status(),
    200,
  );
  assert.equal(
    (
      await reviewer.page.request.get(
        `${root}/data/datasets/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/quality`,
      )
    ).status(),
    404,
  );
  await reviewer.page.goto(
    `${origin}/workspace/data?quality_dataset=${saved.datasetId}`,
  );
  await expect(reviewer.page.locator("#dataset-quality")).toContainText(
    "5 / 5",
  );
  await reviewer.page.reload();
  await expect(reviewer.page.locator(".history-request-form")).toHaveCount(0);
  await expect(
    reviewer.page.getByRole("button", { name: "预览并保存成员", exact: true }),
  ).toHaveCount(0);
  await expect(
    reviewer.page.getByRole("button", {
      name: "保存自定义股票池",
      exact: true,
    }),
  ).toHaveCount(0);
  {
    const response = await reviewer.page.request.get(
      `${root}/data/universes/definitions?limit=1`,
    );
    assert.ok([200, 503].includes(response.status()));
    if (response.status() === 503)
      console.log(
        "Live source catalog unavailable (503); not an integration pass. Persisted campaign read and mutation restrictions checked separately.",
      );
  }
  assert.equal(
    (
      await reviewer.page.request.post(`${root}/data/universes/resolve`, {
        headers: reviewerHeaders,
        data: { universe_id: "mairui:hs300" },
      })
    ).status(),
    403,
  );
  assert.equal(
    (
      await reviewer.page.request.post(`${root}/data/universes/custom`, {
        headers: reviewerHeaders,
        data: { name: "denied", instrument_ids: ["601288.SH"] },
      })
    ).status(),
    403,
  );

  writeFileSync(
    `${directory}/history-permissions-results.txt`,
    "PASS: real non-manager UI read-only / write rejection / unsupported Pro rejection before task creation / quality read / unavailable dataset rejection / refresh\n",
  );
  console.log(
    "Actual history UI and API permissions / unavailable entitlement / immutable quality read: PASS",
  );
} finally {
  await browser.close();
}
