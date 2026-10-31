import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
const identity = JSON.parse(readFileSync("../../.tools/sso-local/state.json"));
const browser = await chromium.launch();
const checks = [];
let context, endpoint, prior, session;
try {
  context = await browser.newContext();
  const page = await context.newPage();
  await page.goto("http://127.0.0.1:13000/workspace/data", { timeout: 90000 });
  await page
    .getByRole("textbox", { name: "邮箱", exact: true })
    .fill("qtrade-integration@example.test");
  await page.getByLabel("密码", { exact: true }).fill(identity.password);
  await page.getByRole("button", { name: "登 录", exact: true }).click();
  await page.waitForURL(
    (url) =>
      url.pathname === "/workspace/data" || url.pathname === "/auth/consent",
    { timeout: 90000 },
  );
  if (page.url().includes("/auth/consent"))
    await page.getByRole("button", { name: "同意授权", exact: true }).click();
  session = await (
    await page.request.get("http://127.0.0.1:3122/auth/session")
  ).json();
  endpoint = `http://127.0.0.1:3122/api/v1/workspaces/${session.workspace_id}/data/collector/preferences`;
  const response = await page.request.get(endpoint);
  assert.equal(response.status(), 200);
  prior = await response.json();
  const panel = page.locator("#independent-collector");
  await panel.getByLabel("采集数据类别", { exact: true }).selectOption("bars");
  await panel
    .getByLabel("采集数据来源", { exact: true })
    .selectOption("mairui.pro.bars");
  await panel.getByLabel("独立采集周期", { exact: true }).selectOption("5m");
  await panel
    .getByLabel("独立采集开始日期", { exact: true })
    .fill("2025-09-01");
  await panel
    .getByLabel("独立采集结束日期", { exact: true })
    .fill("2025-09-05");
  await panel.getByLabel("独立采集标的", { exact: true }).fill("601288.SH");
  const poolCheckbox = panel
    .getByRole("group", { name: "独立采集股票池" })
    .getByRole("checkbox")
    .first();
  await poolCheckbox.check();
  const snapshot = await poolCheckbox.inputValue();
  await panel
    .getByLabel("独立采集下载方式", { exact: true })
    .selectOption("missing");
  await panel.getByRole("button", { name: "保存选择", exact: true }).click();
  await expect(panel).toContainText("已为当前用户和工作空间保存选择");
  const saved = await (await page.request.get(endpoint)).json();
  assert.equal(saved.source, "mairui.pro.bars");
  assert.equal(saved.snapshot_id, snapshot);
  assert.deepEqual(saved.snapshot_ids, [snapshot]);
  assert.equal(saved.instruments, "601288.SH");
  await page.reload({ timeout: 90000 });
  for (const [label, value] of [
    ["采集数据来源", "mairui.pro.bars"],
    ["独立采集周期", "5m"],
    ["独立采集开始日期", "2025-09-01"],
    ["独立采集结束日期", "2025-09-05"],
    ["独立采集标的", "601288.SH"],
    ["独立采集下载方式", "missing"],
  ])
    await expect(panel.getByLabel(label, { exact: true })).toHaveValue(value);
  await expect(
    panel
      .getByRole("group", { name: "独立采集股票池" })
      .getByRole("checkbox", { checked: true }),
  ).toHaveValue(snapshot);
  checks.push({
    name: "deployed_ui_database_save_refresh_all_selections",
    passed: true,
  });
  const denied = await page.request.put(endpoint, { data: saved });
  assert.equal(denied.status(), 403);
  const cross = await page.request.get(
    endpoint.replace(session.workspace_id, randomUUID()),
  );
  assert.equal(cross.status(), 404);
  checks.push({
    name: "actual_csrf_and_unknown_workspace_rejection",
    passed: true,
  });
  const reopened = await context.newPage();
  await reopened.goto("http://127.0.0.1:13000/workspace/data", {
    timeout: 90000,
  });
  await expect(
    reopened
      .locator("#independent-collector")
      .getByLabel("独立采集标的", { exact: true }),
  ).toHaveValue("601288.SH");
  await reopened.close();
  checks.push({
    name: "reopened_page_restores_current_user_draft",
    passed: true,
  });
  const unknownSource = "fixture.removed.source";
  const changed = await page.request.put(endpoint, {
    data: { ...saved, source: unknownSource },
    headers: { "X-CSRF-Token": session.csrf_token },
  });
  assert.equal(changed.status(), 200);
  await page.reload({ timeout: 90000 });
  await expect(panel.getByLabel("采集数据来源", { exact: true })).toHaveValue(
    unknownSource,
  );
  await expect(panel.getByLabel("独立采集周期", { exact: true })).toHaveValue(
    "5m",
  );
  await expect(
    panel.getByRole("button", { name: "预览独立采集计划", exact: true }),
  ).toBeDisabled();
  await expect(panel).toContainText("已保存来源不可用，请重新选择");
  checks.push({
    name: "removed_source_retained_and_execution_disabled",
    passed: true,
  });
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 950 });
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    assert.equal(
      (
        await new AxeBuilder({ page })
          .include("#independent-collector")
          .analyze()
      ).violations.length,
      0,
    );
    checks.push({ name: `layout_accessibility_${width}`, passed: true });
  }
  writeFileSync(
    "../../.tools/verification-local/review/collection-preferences-ui-results.json",
    JSON.stringify(
      {
        checks,
        limitation:
          "Preference persistence only; live role revocation UI and remaining collection flows separately required",
      },
      null,
      2,
    ),
  );
  console.log(JSON.stringify({ checks }));
} finally {
  if (context && endpoint && session) {
    const draft = prior ?? {
      schema_version: 1,
      dataset_kind: "bars",
      source: "mairui.history.bars",
      granularity: "1d",
      first: null,
      last: null,
      instruments: "",
      snapshot_id: null,
      fields: ["pe_ttm"],
      mode: "missing",
    };
    const restored = await context.request.put(endpoint, {
      data: draft,
      headers: { "X-CSRF-Token": session.csrf_token },
    });
    assert.equal(restored.status(), 200, "restore integration-user draft");
  }
  await browser.close();
}
