/** Live authentication continuation, no token injection or mocked identity. */
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
const privateRoot = "../../.tools/verification-local/";
const admin = JSON.parse(readFileSync("../../.tools/sso-local/state.json"));
const reader = JSON.parse(readFileSync(privateRoot + "approver.json"));
const scope = JSON.parse(
  readFileSync(privateRoot + "budget-scope.json"),
).scope_id;
const browser = await chromium.launch();
const results = [];
try {
  for (let round = 0; round < 2; round++) {
    // Four independent browser contexts intentionally overlap password, session,
    // consent, code exchange and native API provisioning for existing identities.
    const jobs = ["admin", "reader", "admin", "reader"].map(
      async (role, index) => {
        const context = await browser.newContext({
          viewport: { width: 1440, height: 1000 },
        });
        const page = await context.newPage();
        const destination =
          "http://127.0.0.1:13000/workspace/data?data_view=monitor&monitor_view=sources";
        let phase = "login";
        try {
          await page.goto(destination, { timeout: 90000 });
          await page
            .getByRole("textbox", { name: "邮箱", exact: true })
            .fill(
              role === "admin"
                ? "qtrade-integration@example.test"
                : reader.email,
            );
          await page
            .getByLabel("密码", { exact: true })
            .fill(role === "admin" ? admin.password : reader.password);
          await page
            .getByRole("button", { name: "登 录", exact: true })
            .click();
          phase = "consent or RP";
          await page.waitForURL(
            (u) =>
              ["/workspace/data", "/auth/consent", "/auth/error"].includes(
                u.pathname,
              ),
            { timeout: 90000 },
          );
          assert(!page.url().includes("/auth/error"));
          if (new URL(page.url()).pathname === "/auth/consent")
            await page
              .getByRole("button", { name: "同意授权", exact: true })
              .click();
          phase = "RP URL";
          await expect(page).toHaveURL(destination, { timeout: 90000 });
          phase = "RP content ready";
          const rendering = performance.now();
          await expect(
            page.getByRole("heading", { name: "数据管理", exact: true }),
          ).toBeVisible({ timeout: 90000 });
          const contentReadyMs = Math.round(performance.now() - rendering);
          phase = "RP selected navigation";
          await expect(
            page.getByRole("tab", { name: "来源与新鲜度", exact: true }),
          ).toHaveAttribute("aria-selected", "true");
          await expect(
            page.getByRole("tab", { name: "来源能力", exact: true }),
          ).toHaveAttribute("aria-selected", "true");
          await expect(
            page
              .getByLabel("切换工作区", { exact: true })
              .locator(`option[value="${scope}"]`),
          ).toHaveCount(1);
          phase = "native authenticated session";
          const response = await page.request.get(
            "http://127.0.0.1:3122/auth/session",
          );
          assert.equal(response.status(), 200);
          assert.equal((await response.json()).authenticated, true);
          return {
            round,
            index,
            role,
            live_sso: true,
            rp_session: true,
            deep_link: true,
            prior_workspace_preserved: true,
            content_ready_ms: contentReadyMs,
          };
        } catch (error) {
          throw new Error(
            `${role} ${round}/${index} ${phase} at ${new URL(page.url()).pathname}: ${error?.name ?? "Error"}`,
          );
        } finally {
          await context.close();
        }
      },
    );
    // Keep every rejection observable; a fast failed branch must not discard
    // remaining live requests or hide partial outcomes from the report.
    const settled = await Promise.allSettled(jobs);
    for (const result of settled) {
      if (result.status === "fulfilled") results.push(result.value);
      else results.push({ failed: true, stage: result.reason.message });
    }
    if (results.some((r) => r.failed)) break;
  }
  writeFileSync(
    privateRoot + "review/sso-redirect-delivery-results.json",
    JSON.stringify({ results }, null, 2),
    { mode: 0o600 },
  );
  assert.equal(results.length, 8);
  assert(!results.some((r) => r.failed));
  console.log(
    "PASS live SSO: eight existing-identity logins, four concurrent contexts, deep links and prior workspaces",
  );
} catch (error) {
  console.error(`FAIL live SSO redirect delivery: ${error?.name ?? "Error"}`);
  process.exitCode = 1;
} finally {
  await browser.close();
}
