import assert from "node:assert/strict";
import { readFileSync, writeFileSync, chmodSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
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
  await expect(page.locator("#independent-collector")).toBeVisible({
    timeout: 90000,
  });
  const session = await (
    await page.request.get("http://127.0.0.1:3122/auth/session")
  ).json();
  return {
    context,
    page,
    scope: session.workspace_id,
    csrf: { "X-CSRF-Token": session.csrf_token },
    root: `http://127.0.0.1:3122/api/v1/workspaces/${session.workspace_id}/data/collector/research`,
  };
}
try {
  const identity = JSON.parse(
    readFileSync("../../.tools/sso-local/state.json"),
  );
  const admin = await login(
    { ...identity, email: "qtrade-integration@example.test" },
    { width: 1440, height: 1000 },
  );
  const rows = await (
    await admin.page.request.get(admin.root + "/jobs")
  ).json();
  const job = rows.find(
    (r) => r.state === "completed" && r.request.kind === "financial_statement",
  );
  assert(job);
  const manifest = await (
    await admin.page.request.get(
      admin.root + "/publications/" + job.publication_id,
    )
  ).json();
  assert.equal(
    (
      await admin.page.request.post(
        `${admin.root}/jobs/${job.request_id}/retry`,
      )
    ).status(),
    403,
  );
  for (let i = 0; i < 2; i++) {
    const response = await admin.page.request.post(
      `${admin.root}/jobs/${job.request_id}/retry`,
      { headers: admin.csrf },
    );
    assert.equal(response.status(), 200);
    const state = await response.json();
    assert.equal(state.state, "completed");
    assert.equal(state.publication_id, job.publication_id);
  }
  const after = await (
    await admin.page.request.get(
      admin.root + "/publications/" + job.publication_id,
    )
  ).json();
  assert.deepEqual(after, manifest);
  checks.push(
    "actual admin / completed retry no-op / immutable publication / repeat idempotence",
  );
  checks.push("actual CSRF rejection");
  const denied = await admin.page.request.post(
    `${admin.root.replace(admin.scope, "00000000-0000-0000-0000-000000000000")}/jobs/${job.request_id}/retry`,
    { headers: admin.csrf },
  );
  assert([403, 404].includes(denied.status()));
  checks.push("cross-scope rejected");
  const anonymous = await browser.newContext();
  assert.equal(
    (
      await anonymous.request.post(`${admin.root}/jobs/${job.request_id}/retry`)
    ).status(),
    401,
  );
  await anonymous.close();
  checks.push("anonymous rejected");
  await admin.context.close();
  const reader = await login(
    JSON.parse(readFileSync("../../.tools/verification-local/approver.json")),
    { width: 390, height: 844 },
  );
  // Login selects this fixture user's own workspace. Select the shared test
  // workspace through the real UI before asserting role (rather than absence).
  await reader.page
    .getByRole("button", { name: "打开主导航", exact: true })
    .click();
  await reader.page
    .getByLabel("切换工作区", { exact: true })
    .selectOption(admin.scope);
  await reader.page.getByRole("button", { name: "切换", exact: true }).click();
  await expect(reader.page).toHaveURL("http://127.0.0.1:13000/", {
    timeout: 60000,
  });
  await reader.page.goto("http://127.0.0.1:13000/workspace/data", {
    timeout: 90000,
  });
  await expect
    .poll(
      async () =>
        (
          await (
            await reader.page.request.get("http://127.0.0.1:3122/auth/session")
          ).json()
        ).workspace_id,
      { timeout: 30000 },
    )
    .toBe(admin.scope);
  const selected = await (
    await reader.page.request.get("http://127.0.0.1:3122/auth/session")
  ).json();
  reader.root = reader.root.replace(reader.scope, selected.workspace_id);
  reader.csrf = { "X-CSRF-Token": selected.csrf_token };
  await expect(
    reader.page.getByRole("region", { name: "采集用量", exact: true }),
  ).toHaveAttribute("data-usage-scope", admin.scope, { timeout: 60000 });
  assert.equal(
    (
      await reader.page.request.post(
        `${reader.root}/jobs/${job.request_id}/retry`,
        { headers: reader.csrf },
      )
    ).status(),
    403,
  );
  await expect(
    reader.page.getByRole("button", { name: "提前重试预算等待", exact: true }),
  ).toHaveCount(0);
  await expect(
    reader.page.getByRole("button", { name: "重试当前任务", exact: true }),
  ).toHaveCount(0);
  await reader.page.reload({ timeout: 90000 });
  await expect(reader.page.locator("#independent-collector")).toBeVisible({
    timeout: 90000,
  });
  checks.push(
    "actual read-only mobile / mutation rejected / no controls / refresh",
  );
  await reader.context.close();
  const file =
    "../../.tools/verification-local/review/research-retry-ui-results.json";
  writeFileSync(
    file,
    JSON.stringify(
      {
        origin: "http://127.0.0.1:13000",
        integration: "actual SSO/API/collector PostgreSQL",
        checks,
        limitation:
          "completed no-op and rejections; does not prove waiting/failed retry UI",
      },
      null,
      2,
    ),
  );
  chmodSync(file, 0o600);
  console.log(`PASS research retry permissions: ${checks.length} checks`);
} finally {
  await browser.close();
}
