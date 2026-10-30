import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
const read = (path) => JSON.parse(readFileSync(`../../.tools/${path}`));
const baseline = read("verification-local/review/collection-retest-ui.json");
const admin = read("sso-local/state.json"),
  reader = read("verification-local/approver.json");
const run = baseline.runs[0],
  pub = run.parts[0].publication_id;
const origin = "http://127.0.0.1:13000";
const root = `http://127.0.0.1:3122/api/v1/workspaces/${baseline.scope_id}/data/collector`;
const endpoint = `${root}/publications/${pub}/freshness/watch`;
const browser = await chromium.launch();
let saved;
let adminPage;
const checks = [];
try {
  for (const role of ["admin", "reader"]) {
    const context = await browser.newContext({
      viewport:
        role === "admin"
          ? { width: 1440, height: 1000 }
          : { width: 390, height: 844 },
    });
    const page = await context.newPage();
    if (role === "admin") adminPage = page;
    console.log(`Checking ${role} watch UI`);
    await page.goto(`${origin}/workspace/data`);
    await page
      .getByRole("textbox", { name: "邮箱", exact: true })
      .fill(
        role === "admin" ? "qtrade-integration@example.test" : reader.email,
      );
    await page
      .getByLabel("密码", { exact: true })
      .fill(role === "admin" ? admin.password : reader.password);
    await page.getByRole("button", { name: "登 录", exact: true }).click();
    await page.waitForURL(
      (u) => ["/workspace/data", "/auth/consent"].includes(u.pathname),
      { timeout: 90000 },
    );
    if (page.url().includes("/auth/consent"))
      await page.getByRole("button", { name: "同意授权", exact: true }).click();
    await expect(page).toHaveURL(`${origin}/workspace/data`, {
      timeout: 90000,
    });
    if (!(await page.getByLabel("切换工作区", { exact: true }).isVisible()))
      await page
        .getByRole("button", { name: "打开主导航", exact: true })
        .click();
    await page
      .getByLabel("切换工作区", { exact: true })
      .selectOption(baseline.scope_id);
    await page.getByRole("button", { name: "切换", exact: true }).click();
    await expect(page).toHaveURL(`${origin}/`, { timeout: 60000 });
    await page.goto(`${origin}/workspace/data?collector_job=${run.job_id}`);
    const panel = page.locator(`[data-freshness-watch="${pub}"]`);
    await expect(panel).toBeVisible();
    await panel.locator("summary").click();
    await panel
      .getByRole("button", { name: "查看后台核验", exact: true })
      .click();
    const session = await (
      await page.request.get("http://127.0.0.1:3122/auth/session")
    ).json();
    if (role === "admin") {
      const existing = await page.request.get(endpoint);
      if (existing.status() === 404) {
        await panel
          .getByRole("button", { name: "配置每15分钟核验", exact: true })
          .click();
      } else {
        assert.equal(existing.status(), 200);
      }
      await expect(
        panel.getByRole("button", { name: /以我身份启用|停用后台核验/ }),
      ).toBeVisible();
      saved = await (await page.request.get(endpoint)).json();
      if (saved.enabled) {
        await panel
          .getByRole("button", { name: "停用后台核验", exact: true })
          .click();
        await expect(
          panel.getByRole("button", { name: "以我身份启用", exact: true }),
        ).toBeVisible();
      }
      const before = await (await page.request.get(endpoint)).json();
      assert.equal(before.enabled, false);
      await panel
        .getByRole("button", { name: "以我身份启用", exact: true })
        .focus();
      await page.keyboard.press("Enter");
      await expect(
        panel.getByRole("button", { name: "停用后台核验", exact: true }),
      ).toBeVisible();
      await expect
        .poll(
          async () => {
            const value = await (await page.request.get(endpoint)).json();
            return (
              value.last_checked_at &&
              value.last_checked_at !== before.last_checked_at &&
              value.last_error_code === null
            );
          },
          { timeout: 60000 },
        )
        .toBeTruthy();
      saved = await (await page.request.get(endpoint)).json();
      const snapshot = await (
        await page.request.get(`${root}/publications/${pub}/freshness`)
      ).json();
      assert.equal(snapshot.snapshot.status, "unknown");
      assert.equal(
        snapshot.snapshot.evidence.published.complete_through,
        "2025-12-31",
      );
      assert.equal(snapshot.watch_issue, null);
      if (process.env.QTRADE_VERIFY_WATCH_RESTART === "true") {
        execFileSync(
          "docker",
          [
            "compose",
            "--env-file",
            ".env.collector",
            "-f",
            "compose.collector.yaml",
            "-f",
            "compose.collector.local.yaml",
            "restart",
            "--timeout",
            "20",
            "collector",
          ],
          { cwd: "../..", stdio: "pipe", timeout: 60000 },
        );
        await expect
          .poll(
            async () => {
              const response = await page.request.get(endpoint);
              return response.status();
            },
            { timeout: 60000 },
          )
          .toBe(200);
        const resumed = await (await page.request.get(endpoint)).json();
        assert.equal(resumed.enabled, true);
        assert.equal(resumed.last_checked_at, saved.last_checked_at);
        assert.equal(resumed.next_due_at, saved.next_due_at);
        assert.equal(resumed.revision, saved.revision);
        checks.push(
          "actual collector restart: enabled watch, next due and completed check retained without duplicate immediate check",
        );
      }

      await panel
        .getByRole("button", { name: "刷新后台核验状态", exact: true })
        .click();
      await expect(panel).toContainText("每 900 秒核验");
      await panel
        .getByRole("button", { name: "停用后台核验", exact: true })
        .click();
      await expect(
        panel.getByRole("button", { name: "以我身份启用", exact: true }),
      ).toBeVisible();
      await page.waitForTimeout(6000);
      const stopped = await (await page.request.get(endpoint)).json();
      assert.equal(stopped.enabled, false);
      assert.equal(stopped.last_checked_at, saved.last_checked_at);
      saved = stopped;
      assert.equal(
        (
          await page.request.post(
            `${root}/freshness/watches/${saved.watch_id}/control`,
            { data: { revision: saved.revision, enabled: true } },
          )
        ).status(),
        403,
      );
      checks.push(
        "admin: actual archived data, enabled timer consumed, unknown not false healthy, stopped and persisted without extra check",
      );
    } else {
      await expect(panel).toContainText("已停用");
      await expect(
        panel.getByRole("button", {
          name: /以我身份启用|停用后台核验|配置每15分钟核验/,
        }),
      ).toHaveCount(0);
      assert.equal(
        (
          await page.request.post(endpoint, {
            headers: { "X-CSRF-Token": session.csrf_token },
          })
        ).status(),
        403,
      );
      assert.equal(
        (
          await page.request.post(
            `${root}/freshness/watches/${saved.watch_id}/control`,
            {
              headers: { "X-CSRF-Token": session.csrf_token },
              data: { revision: saved.revision, enabled: true },
            },
          )
        ).status(),
        403,
      );
      checks.push(
        "risk_manager: collection READ can inspect watch; controls hidden and direct CSRF-authenticated writes rejected",
      );
    }
    await page.reload();
    await panel.locator("summary").click();
    await panel
      .getByRole("button", { name: "查看后台核验", exact: true })
      .click();
    await expect(panel).toContainText("已停用");
    assert.equal(
      (await (await page.request.get(endpoint)).json()).last_checked_at,
      saved.last_checked_at,
    );
    assert.equal(
      (
        await new AxeBuilder({ page })
          .include(`[data-freshness-watch="${pub}"]`)
          .analyze()
      ).violations.length,
      0,
    );
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    await panel.scrollIntoViewIfNeeded();
    await page.screenshot({
      path: `../../.tools/verification-local/review/freshness-watch-${role}.png`,
    });
    assert(
      [403, 404].includes(
        (
          await page.request.get(
            endpoint.replace(
              baseline.scope_id,
              "00000000-0000-4000-8000-000000000001",
            ),
          )
        ).status(),
      ),
    );
    await context.close();
  }
  const anonymous = await browser.newContext();
  assert.equal((await anonymous.request.get(endpoint)).status(), 401);
  await anonymous.close();
  checks.push("anonymous watch query rejected");
  writeFileSync(
    "../../.tools/verification-local/review/freshness-watch-ui-results.json",
    JSON.stringify(
      {
        checks,
        watch: saved,
        real_archives: true,
        price_download: false,
        collector_restart: process.env.QTRADE_VERIFY_WATCH_RESTART === "true",
      },
      null,
      2,
    ),
    { mode: 0o600 },
  );
  console.log(
    "PASS actual watch timer and admin/reader desktop/mobile persisted UI",
  );
} finally {
  // This script owns only the dedicated validation watch. On a failed test,
  // stop it through the same authenticated interface instead of leaving a
  // hidden background workload or changing unrelated watches.
  if (adminPage && !adminPage.isClosed()) {
    try {
      const watch = await (await adminPage.request.get(endpoint)).json();
      if (watch.enabled && watch.watch_id) {
        const session = await (
          await adminPage.request.get("http://127.0.0.1:3122/auth/session")
        ).json();
        const response = await adminPage.request.post(
          `${root}/freshness/watches/${watch.watch_id}/control`,
          {
            headers: { "X-CSRF-Token": session.csrf_token },
            data: { revision: watch.revision, enabled: false },
          },
        );
        assert.equal(response.status(), 200);
      }
    } catch {
      console.error("Validation watch cleanup could not be confirmed");
      process.exitCode = 1;
    }
  }
  await browser.close();
}
