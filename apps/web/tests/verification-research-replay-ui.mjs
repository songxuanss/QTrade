/** Real archived supplier inputs -> deployed UI/API/PG/Worker/publication.
 * No new supplier fetch; controlled normalization failures remain separate. */
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { openDataView } from "./data-workbench-navigation.mjs";
const read = (path) => JSON.parse(readFileSync(`../../.tools/${path}`));
const admin = read("sso-local/state.json");
const reader = read("verification-local/approver.json");
const readOnly = process.argv.includes("--read-only");
const targets = readOnly
  ? read("verification-local/review/research-replay-ui.json").targets
  : [
      {
        ...read("verification-local/review/financial-ui-results.json").download,
        kind: "financial_statement",
        tab: "财务报表",
        panel: "#financial-collection",
        browse: "浏览财务版本",
      },
      {
        ...read("verification-local/review/distribution-ui-results.json")
          .download,
        kind: "corporate_action",
        tab: "分红、送转与配股",
        panel: "#distribution-collection",
        browse: "浏览公司行为版本",
      },
    ];
const scope = targets[0].scope_id;
assert(targets.every((t) => t.scope_id === scope));
const origin = "http://127.0.0.1:13000";
const base = `http://127.0.0.1:3122/api/v1/workspaces/${scope}/data/collector`;
const browser = await chromium.launch();
const checks = [];
let phase = "login";
let active;
async function json(page, path) {
  const response = await page.request.get(`${base}/${path}`);
  assert.equal(response.status(), 200);
  return response.json();
}
async function quality(page) {
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    const axe = await new AxeBuilder({ page })
      .include("dialog[open]")
      .analyze();
    assert.equal(
      axe.violations.length,
      0,
      axe.violations.map((v) => v.id).join(","),
    );
  }
}
try {
  for (const role of ["admin", "reader"]) {
    const context = await browser.newContext({
      viewport: { width: role === "admin" ? 1440 : 390, height: 1000 },
    });
    const page = await context.newPage();
    active = page;
    let browserErrors = 0;
    page.on("pageerror", () => browserErrors++);
    page.setDefaultTimeout(30000);
    phase = role + " real SSO";
    await page.goto(origin + "/workspace/data", { timeout: 90000 });
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
    if (new URL(page.url()).pathname === "/auth/consent")
      await page.getByRole("button", { name: "同意授权", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "数据管理", exact: true }),
    ).toBeVisible({ timeout: 90000 });
    if (!(await page.getByLabel("切换工作区", { exact: true }).isVisible()))
      await page
        .getByRole("button", { name: "打开主导航", exact: true })
        .click();
    await page.getByLabel("切换工作区", { exact: true }).selectOption(scope);
    await page.getByRole("button", { name: "切换", exact: true }).click();
    await expect(page).toHaveURL(origin + "/", { timeout: 60000 });
    await page.goto(origin + "/workspace/data", { timeout: 90000 });
    const session = await (
      await page.request.get("http://127.0.0.1:3122/auth/session")
    ).json();
    const csrf = { "X-CSRF-Token": session.csrf_token };
    const safeDraft = {
      request_id: crypto.randomUUID(),
      parent_request_id: targets[0].request_id,
    };
    assert.equal(
      (
        await page.request.post(`${base}/research/replays/preview`, {
          data: safeDraft,
        })
      ).status(),
      403,
    );
    assert.equal(
      (
        await page.request.post(`${base}/research/replays/preview`, {
          headers: csrf,
          data: { ...safeDraft, actor_id: crypto.randomUUID() },
        })
      ).status(),
      role === "admin" ? 422 : 403,
    );
    const other = await page.request.get(
      `http://127.0.0.1:3122/api/v1/workspaces/${crypto.randomUUID()}/data/collector/research/replays/${safeDraft.request_id}`,
    );
    assert([403, 404].includes(other.status()));
    const anonymous = await browser.newContext();
    assert(
      [401, 403].includes(
        (
          await anonymous.request.get(
            `${base}/research/replays/${safeDraft.request_id}`,
          )
        ).status(),
      ),
    );
    await anonymous.close();

    for (const target of targets) {
      phase = role + " " + target.kind + " modal";
      await openDataView(page, "多维下载", target.tab);
      const beforeUsage = await json(page, "usage");
      const parent = await json(
        page,
        `research/publications/${target.publication_id}`,
      );
      const panel = page.locator(target.panel);
      const create = role === "admin" && !readOnly;
      const id = create ? target.request_id : target.replay_id;
      const row = panel.locator(`[data-research-job="${id}"]`);
      await row
        .getByRole("button", {
          name: role === "admin" ? "RAW 重处理" : "重处理血缘",
          exact: true,
        })
        .focus();
      await page.keyboard.press("Enter");
      const dialog = page.getByRole("dialog", {
        name: "研究 RAW 重处理",
        exact: true,
      });
      await expect(dialog).toBeVisible();
      await expect(dialog).toContainText("不访问供应商");
      if (create) {
        const jobsBefore = await json(page, "research/jobs");
        phase = role + " " + target.kind + " interrupted preview";
        const interrupted = async (route) => {
          if (
            route.request().method() === "POST" &&
            route.request().headers()["next-action"]
          )
            await route.abort();
          else await route.continue();
        };
        await page.route("**/workspace/data*", interrupted);
        await dialog
          .getByRole("button", { name: "预览当前 RAW", exact: true })
          .click();
        await expect(dialog.getByRole("alert")).toContainText("暂不可用");
        await page.unroute("**/workspace/data*", interrupted);
        await expect(
          dialog.getByRole("button", { name: "预览当前 RAW", exact: true }),
        ).toBeEnabled();
        phase = role + " " + target.kind + " preview";

        await dialog
          .getByRole("button", { name: "预览当前 RAW", exact: true })
          .click();
        await expect(dialog.locator("[data-replay-request]")).toBeVisible({
          timeout: 90000,
        });
        target.replay_id = await dialog
          .locator("[data-replay-request]")
          .getAttribute("data-replay-request");
        assert(target.replay_id && target.replay_id !== target.request_id);
        assert.deepEqual(await json(page, "research/jobs"), jobsBefore);
        await expect(dialog).toContainText("供应商调用 0 次");
        await expect(dialog).toContainText(`${target.records} 条原始记录`);
        await quality(page);
        await dialog.screenshot({
          path: `../../.tools/verification-local/review/replay-${target.kind}-preview-mobile.png`,
        });
        await page.setViewportSize({ width: 1440, height: 1000 });
        phase = role + " " + target.kind + " submit and worker";
        writeFileSync(
          "../../.tools/verification-local/review/research-replay-ui-attempt.json",
          JSON.stringify({ scope_id: scope, phase, targets, checks }, null, 2),
          { mode: 0o600 },
        );
        await dialog
          .getByRole("button", { name: "确认创建重处理任务", exact: true })
          .click();
        await expect(dialog.locator("[data-replay-state]")).toBeVisible({
          timeout: 90000,
        });
        await expect
          .poll(
            async () =>
              (await json(page, `research/jobs/${target.replay_id}`)).state,
            { timeout: 90000, intervals: [500, 1000] },
          )
          .toBe("completed");
        await dialog
          .getByRole("button", { name: "刷新处理结果", exact: true })
          .click();
        await expect(
          dialog.locator('[data-replay-state="completed"]'),
        ).toBeVisible({ timeout: 90000 });
        const fresh = await json(page, `research/jobs/${target.replay_id}`);
        assert.notEqual(fresh.publication_id, target.publication_id);
        target.replay_publication_id = fresh.publication_id;
        const intent = await json(page, `research/replays/${target.replay_id}`);
        assert.equal(intent.command.parent_request_id, target.request_id);
        const repeat = await page.request.post(`${base}/research/replays`, {
          headers: csrf,
          data: {
            request_id: target.replay_id,
            parent_request_id: target.request_id,
            control_sha256: intent.control_sha256,
          },
        });
        assert.equal(repeat.status(), 201);
        assert.equal((await repeat.json()).state, "completed");
        const afterJobs = await json(page, "research/jobs");
        assert.equal(afterJobs.length, jobsBefore.length + 1);
        target.control_sha256 = intent.control_sha256;
      } else {
        if (role === "reader")
          assert.equal(
            await dialog
              .getByRole("button", { name: "预览当前 RAW", exact: true })
              .count(),
            0,
          );
        await dialog
          .getByRole("button", { name: "读取当前任务血缘", exact: true })
          .click();
        await expect(dialog.locator("[data-replay-request]")).toHaveAttribute(
          "data-replay-request",
          target.replay_id,
          { timeout: 90000 },
        );
        await expect(dialog).toContainText(target.request_id);
        await quality(page);
        await dialog.screenshot({
          path: `../../.tools/verification-local/review/replay-${target.kind}-${role}-lineage-mobile.png`,
        });
        for (const path of role === "reader"
          ? ["replays/preview", "replays"]
          : []) {
          const response = await page.request.post(`${base}/research/${path}`, {
            headers: csrf,
            data: {
              request_id: target.replay_id,
              parent_request_id: target.request_id,
              ...(path === "replays"
                ? { control_sha256: target.control_sha256 }
                : {}),
            },
          });
          assert.equal(response.status(), 403);
        }
      }
      const newManifest = await json(
        page,
        `research/publications/${target.replay_publication_id}`,
      );
      assert.equal(newManifest.artifact.record_count, target.records);
      assert.equal(newManifest.artifact.request_id, target.replay_id);
      assert(["passed", "warning"].includes(newManifest.quality.status));
      assert.deepEqual(
        await json(page, `research/publications/${target.publication_id}`),
        parent,
      );
      const afterUsage = await json(page, "usage");
      assert.equal(
        afterUsage.scope_budget.credits_used,
        beforeUsage.scope_budget.credits_used,
      );
      assert.equal(afterUsage.request_count, beforeUsage.request_count);
      await dialog.getByRole("button", { name: "关闭", exact: true }).click();
      phase = role + " " + target.kind + " consumer button";
      const newRow = panel.locator(`[data-research-job="${target.replay_id}"]`);
      await expect(newRow).toContainText("已发布");
      await newRow
        .getByRole("button", { name: target.browse, exact: true })
        .click();
      phase = role + " " + target.kind + " consumer records";
      await expect(
        panel.locator("[data-research-versions] > details"),
      ).toHaveCount(target.records, { timeout: 30000 });
      await expect(panel.locator("[data-research-versions]")).toContainText(
        newManifest.content_version,
      );
      phase = role + " " + target.kind + " reload";
      await page.reload({ timeout: 90000 });
      await expect(newRow).toContainText("已发布");
      await newRow
        .getByRole("button", { name: target.browse, exact: true })
        .click();
      phase = role + " " + target.kind + " consumer records";
      await expect(
        panel.locator("[data-research-versions] > details"),
      ).toHaveCount(target.records, { timeout: 30000 });
      checks.push({
        role,
        kind: target.kind,
        new_request: target.replay_id,
        records: target.records,
        real_archived_supplier_data: true,
        new_supplier_requests: 0,
        consumer_refresh: true,
        axe_violations: 0,
      });
    }
    assert.equal(browserErrors, 0, "unexpected browser script errors");
    await context.close();
  }
  writeFileSync(
    `../../.tools/verification-local/review/research-replay-ui${readOnly ? "-read-only" : ""}.json`,
    JSON.stringify(
      { scope_id: scope, read_only: readOnly, targets, checks },
      null,
      2,
    ),
    { mode: 0o600 },
  );
  console.log(
    "PASS research replay: real archived inputs, UI/API/PG/Worker/new publication, old references, zero scoped supplier calls, roles, reload and desktop/mobile axe0",
  );
} catch (error) {
  writeFileSync(
    "../../.tools/verification-local/review/research-replay-ui-attempt.json",
    JSON.stringify(
      { scope_id: scope, phase, targets, checks, failed: true },
      null,
      2,
    ),
    { mode: 0o600 },
  );
  console.error(`FAIL research replay at ${phase}: ${error?.name ?? "Error"}`);
  if (
    active &&
    !active.isClosed() &&
    new URL(active.url()).pathname === "/workspace/data"
  )
    await active
      .screenshot({
        path: "../../.tools/verification-local/review/research-replay-diagnostic.png",
      })
      .catch(() => {});
  process.exitCode = 1;
} finally {
  await browser.close();
}
