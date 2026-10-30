import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { chromium, expect } from "@playwright/test";

const origin =
  process.env.QTRADE_VERIFICATION_ORIGIN ?? "http://127.0.0.1:13000";
const api = "http://127.0.0.1:3122";
const namespace =
  process.env.QTRADE_ACCESS_REPORT ?? "collector-access-results";
assert(/^[a-z0-9-]+$/.test(namespace));
const identity = JSON.parse(readFileSync("../../.tools/sso-local/state.json"));
const browser = await chromium.launch();
const results = {
  origin,
  api,
  integration: "real SSO / deployed Compose BFF / persisted API",
  checks: [],
};
try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  const page = await context.newPage();
  const failures = [];
  page.on("pageerror", () => failures.push("browser_error"));
  await page.goto(`${origin}/workspace/data`, { timeout: 90000 });
  await page
    .getByRole("textbox", { name: "邮箱", exact: true })
    .fill("qtrade-integration@example.test");
  await page.getByLabel("密码", { exact: true }).fill(identity.password);
  await page.getByRole("button", { name: "登 录", exact: true }).click();
  await page.waitForURL(
    (url) =>
      url.href === `${origin}/workspace/data` ||
      url.pathname === "/auth/consent",
    { timeout: 90000 },
  );
  if (page.url().includes("/auth/consent"))
    await page.getByRole("button", { name: "同意授权", exact: true }).click();
  await expect(page).toHaveURL(`${origin}/workspace/data`, { timeout: 90000 });
  for (const viewport of [
    { width: 1440, height: 1000 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(viewport);
    await page.goto(`${origin}/workspace/settings`, { timeout: 90000 });
    const nav = page.locator('a[href="/workspace/data"]').first();
    if (!(await nav.isVisible())) {
      await page
        .getByRole("button", { name: /导航|菜单/ })
        .first()
        .click();
    }
    await nav.click();
    await expect(page).toHaveURL(`${origin}/workspace/data`, {
      timeout: 90000,
    });
    await expect(page.locator("#independent-collector")).toContainText(
      "远程服务已连接",
      { timeout: 90000 },
    );
    await page.reload({ timeout: 90000 });
    await expect(page.locator("#independent-collector")).toBeVisible();
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    results.checks.push(
      `navigation / direct route / refresh / no overflow ${viewport.width}`,
    );
  }
  const cookie = (await context.cookies()).find(
    (c) => c.name === "qtrade-session",
  );
  assert.ok(cookie);
  const hashed = createHash("sha256").update(cookie.value).digest("hex");
  const expiration = spawnSync(
    "../../.venv/bin/python",
    [
      "-c",
      `
import json,sys,psycopg
from pathlib import Path
state=json.loads(Path('../../.tools/sso-local/state.json').read_text())
with psycopg.connect(state['api_url']) as conn:
 row=conn.execute("UPDATE session SET expires_at=now()-interval '1 second' WHERE session_digest=%s AND created_at<now()-interval '1 second' RETURNING user_id", (sys.stdin.read(),)).fetchone()
 assert row is not None
`,
    ],
    { input: hashed, encoding: "utf8" },
  );
  assert.equal(expiration.status, 0, "could not expire this test session");
  assert.equal((await page.request.get(`${api}/auth/session`)).status(), 401);
  await page.goto(`${origin}/workspace/data`, { timeout: 90000 });
  await expect(page).toHaveURL(`${origin}/workspace/data`, { timeout: 90000 });
  await expect(page.locator("#independent-collector")).toContainText(
    "远程服务已连接",
    { timeout: 90000 },
  );
  const renewed = (await context.cookies()).find(
    (c) => c.name === "qtrade-session",
  );
  assert.notEqual(renewed?.value, cookie.value);
  assert.equal((await page.request.get(`${api}/auth/session`)).status(), 200);
  results.checks.push(
    "real database session expiration / SSO reauthentication / return to data",
  );
  assert.deepEqual(failures, []);
  writeFileSync(
    `../../.tools/verification-local/review/${namespace}.json`,
    JSON.stringify(results, null, 2),
  );
  console.log(
    `PASS deployed collector access: ${results.checks.length} checks`,
  );
} catch {
  // Never render Playwright failure arguments: a failed login fill can contain
  // credentials. This live runner reports only a fixed public failure code.
  console.error("FAIL deployed collector access; private diagnostics withheld");
  process.exitCode = 1;
} finally {
  await browser.close();
}
