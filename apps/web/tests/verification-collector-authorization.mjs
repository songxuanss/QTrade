import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";

// Uses only the isolated SSO identities. No supplier requests or fabricated
// datasets are needed to prove authorization before a collection can start.
const origin = "http://127.0.0.1:13000";
const api = "http://127.0.0.1:3122";
const ownerIdentity = JSON.parse(
  readFileSync("../../.tools/sso-local/state.json"),
);
const readerIdentity = JSON.parse(
  readFileSync("../../.tools/verification-local/approver.json"),
);
const checks = [];
let stage = "launch";
const browser = await chromium.launch();
try {
  async function login(email, password) {
    const context = await browser.newContext();
    const page = await context.newPage();
    await page.goto(`${origin}/workspace/data`, { timeout: 90000 });
    await page.getByRole("textbox", { name: "邮箱", exact: true }).fill(email);
    await page.getByLabel("密码", { exact: true }).fill(password);
    await page.getByRole("button", { name: "登 录", exact: true }).click();
    await page.waitForURL(
      (u) =>
        u.href === `${origin}/workspace/data` || u.pathname === "/auth/consent",
      { timeout: 90000 },
    );
    if (page.url().includes("/auth/consent"))
      await page.getByRole("button", { name: "同意授权", exact: true }).click();
    await expect(page).toHaveURL(`${origin}/workspace/data`, {
      timeout: 90000,
    });
    const response = await page.request.get(`${api}/auth/session`);
    assert.equal(response.status(), 200);
    return { context, page, session: await response.json() };
  }
  stage = "owner-login";
  const owner = await login(
    "qtrade-integration@example.test",
    ownerIdentity.password,
  );
  stage = "reader-login";
  const reader = await login(readerIdentity.email, readerIdentity.password);
  const root = `${api}/api/v1/workspaces/${owner.session.workspace_id}`;
  const collector = `${root}/data/collector`;
  const headers = { "X-CSRF-Token": owner.session.csrf_token };
  const membership = `${root}/members/${reader.session.user_id}`;
  stage = "anonymous-and-cross-scope";
  const anonymous = await browser.newContext();
  const anonymousStatus = (
    await anonymous.request.get(`${collector}/jobs`)
  ).status();
  stage = `anonymous-status-${anonymousStatus}`;
  assert.equal(anonymousStatus, 401);
  // Revoke first so reruns also establish the denied baseline independently.
  const revokedStatus = (
    await owner.page.request.put(membership, {
      headers,
      data: { role: "risk_manager", active: false },
    })
  ).status();
  stage = `baseline-revocation-status-${revokedStatus}`;
  assert.equal(revokedStatus, 200);
  const crossStatus = (
    await reader.page.request.get(`${collector}/jobs`)
  ).status();
  stage = `cross-scope-status-${crossStatus}`;
  assert.equal(crossStatus, 404);
  checks.push("anonymous401 / cross-workspace404");
  stage = "csrf";
  assert.equal(
    (
      await owner.page.request.put(membership, {
        data: { role: "risk_manager", active: true },
      })
    ).status(),
    403,
  );
  assert.equal(
    (await reader.page.request.get(`${collector}/jobs`)).status(),
    404,
  );
  checks.push("missing CSRF403 / membership unchanged");
  stage = "grant";
  assert.equal(
    (
      await owner.page.request.put(membership, {
        headers,
        data: { role: "risk_manager", active: true },
      })
    ).status(),
    200,
  );
  await reader.page.reload({ timeout: 90000 });
  const switcher = reader.page.getByLabel("切换工作区", { exact: true });
  if (!(await switcher.isVisible()))
    await reader.page
      .getByRole("button", { name: "打开主导航", exact: true })
      .click();
  await switcher.selectOption(owner.session.workspace_id);
  await reader.page.getByRole("button", { name: "切换", exact: true }).click();
  await expect(reader.page).toHaveURL(`${origin}/`, { timeout: 90000 });
  const session = await (
    await reader.page.request.get(`${api}/auth/session`)
  ).json();
  assert.equal(session.workspace_id, owner.session.workspace_id);
  const before = await owner.page.request.get(`${collector}/jobs`);
  assert.equal(before.status(), 200);
  const baseline = await before.json();
  for (const width of [1440, 390]) {
    stage = `reader-${width}`;
    await reader.page.setViewportSize({ width, height: 1000 });
    await reader.page.goto(`${origin}/workspace/data`, { timeout: 90000 });
    await reader.page.reload({ timeout: 90000 });
    const panel = reader.page.locator("#independent-collector");
    stage = `reader-connected-${width}`;
    await expect(panel).toContainText("远程服务已连接", { timeout: 90000 });
    stage = `reader-disabled-${width}`;
    await expect(
      panel.getByRole("button", { name: "预览独立采集计划", exact: true }),
    ).toHaveCount(0);
    await expect(panel).toContainText("当前为只读访问");
    stage = `reader-read-${width}`;
    assert.equal(
      (await reader.page.request.get(`${collector}/jobs`)).status(),
      200,
    );
    stage = `reader-write-${width}`;
    assert.equal(
      (
        await reader.page.request.post(`${collector}/jobs`, {
          headers: { "X-CSRF-Token": session.csrf_token },
          data: {},
        })
      ).status(),
      403,
    );
    stage = `reader-overflow-${width}`;
    assert.ok(
      await reader.page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    checks.push(
      `reader read200 / mutation403 / no write control / refresh / no overflow ${width}`,
    );
  }
  stage = "persistence";
  const after = await owner.page.request.get(`${collector}/jobs`);
  assert.equal(after.status(), 200);
  assert.deepEqual(await after.json(), baseline);
  checks.push("rejected mutations leave persisted jobs unchanged");
  stage = "revocation";
  assert.equal(
    (
      await owner.page.request.put(membership, {
        headers,
        data: { role: "risk_manager", active: false },
      })
    ).status(),
    200,
  );
  const revokedRead = (
    await reader.page.request.get(`${collector}/jobs`)
  ).status();
  stage = `revoked-read-${revokedRead}`;
  // Revoking a selected membership also invalidates its existing session;
  // reauthentication may establish a personal session but cannot restore it.
  assert.equal(revokedRead, 401);
  stage = "revoked-refresh";
  await reader.page.reload({ timeout: 90000 });
  const afterRefresh = (
    await reader.page.request.get(`${collector}/jobs`)
  ).status();
  assert.ok([401, 404].includes(afterRefresh));
  checks.push(
    `revocation invalidates session401 / remains denied after refresh${afterRefresh}`,
  );
  writeFileSync(
    "../../.tools/verification-local/review/windows-collector-authorization.json",
    JSON.stringify(
      {
        origin,
        integration: "real SSO / Compose BFF / API / PostgreSQL / collector",
        checks,
        supplier: "not exercised",
        limitations: "No live download or publication acceptance",
      },
      null,
      2,
    ),
  );
  console.log(`PASS collector authorization: ${checks.length} groups`);
} catch {
  // Playwright's fill/assert diagnostics may include private input or session
  // data. Emit only our fixed stage names; no traces or authentication images.
  console.error(
    `FAIL collector authorization at ${stage}; private diagnostics withheld`,
  );
  process.exitCode = 1;
} finally {
  await browser.close();
}
