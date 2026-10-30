import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
const identity = JSON.parse(readFileSync("../../.tools/sso-local/state.json"));
const browser = await chromium.launch();
const checks = [];

try {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto("http://127.0.0.1:13000/workspace/data", { timeout: 90000 });
  await page
    .getByRole("textbox", { name: "邮箱", exact: true })
    .fill("qtrade-integration@example.test");
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
  const root = `http://127.0.0.1:3122/api/v1/workspaces/${session.workspace_id}/data/collector`;
  const started = Date.now();
  const response = await page.request.get(`${root}/factor-catalog`);
  assert.equal(response.status(), 200);
  const snapshot = await response.json();
  const durationMs = Date.now() - started;
  assert.equal(snapshot.schema_version, 1);
  assert.equal(snapshot.permission_evidence, "not_established_by_catalog");
  assert(["fresh", "stale"].includes(snapshot.status));
  assert(snapshot.definitions.length > 0);
  checks.push({
    name: "real_persisted_catalog_public_read",
    passed: true,
    status: snapshot.status,
    definitions: snapshot.definitions.length,
    content_sha256: snapshot.content_sha256,
    duration_ms: durationMs,
    error_code: snapshot.error_code,
    fetched_at: snapshot.fetched_at,
  });
  await page
    .getByLabel("采集数据类别", { exact: true })
    .selectOption("factors");
  const status = page.getByLabel("因子目录状态", { exact: true });
  await expect(status).toBeVisible();
  await expect(status).toContainText("历史权限与范围以采集响应为准");
  await expect(page.getByLabel("历史因子字段列表")).toContainText(
    snapshot.definitions[0].name,
  );
  const pe = snapshot.definitions.find((d) => d.factor_id === "pe_ttm");
  assert(pe, "fixed real catalog must contain the acceptance factor");
  const peCard = page.locator('[data-factor-id="pe_ttm"]');
  await peCard.getByRole("checkbox").check();
  const selectedIds = async () =>
    page
      .getByLabel("已选因子", { exact: true })
      .getByRole("button")
      .evaluateAll((nodes) =>
        nodes.map((n) => n.getAttribute("aria-label")).sort(),
      );
  const selection = await selectedIds();
  await page.getByLabel("因子分类", { exact: true }).selectOption(pe.category);
  assert.deepEqual(
    await page
      .locator("[data-factor-id]")
      .evaluateAll((nodes) => nodes.map((n) => n.dataset.factorId).sort()),
    snapshot.definitions
      .filter((d) => d.category === pe.category)
      .map((d) => d.factor_id)
      .sort(),
  );
  await page.getByLabel("搜索因子", { exact: true }).fill("pe_ttm");
  await expect(page.locator("[data-factor-id]")).toHaveCount(1);
  await expect(peCard.getByRole("checkbox")).toBeChecked();
  await peCard.getByText("查看定义与元数据", { exact: true }).click();
  await expect(peCard).toContainText(
    pe.description?.trim() || "来源未提供定义说明。",
  );
  for (const value of [pe.unit, pe.update_frequency, pe.calc_version])
    await expect(peCard).toContainText(
      value && value !== "unknown" ? value : "未知（来源未提供）",
    );
  await expect(peCard).toContainText("未由目录证明");
  checks.push({
    name: "factor_category_search_definition_frequency_version",
    passed: true,
  });
  await page
    .getByLabel("搜索因子", { exact: true })
    .fill("__no_matching_factor__");
  await expect(page.locator("[data-factor-id]")).toHaveCount(0);
  await expect(page.getByLabel("历史因子字段列表")).toContainText(
    "没有匹配的因子",
  );
  assert.deepEqual(await selectedIds(), selection);
  await page.getByLabel("因子分类", { exact: true }).selectOption("");
  await page.getByLabel("搜索因子", { exact: true }).fill("");
  await expect(page.locator("[data-factor-id]")).toHaveCount(
    snapshot.definitions.length,
  );
  assert.deepEqual(await selectedIds(), selection);
  checks.push({
    name: "filters_and_empty_search_preserve_explicit_selection",
    passed: true,
  });
  await page
    .getByRole("button", { name: "移除因子 pe_ttm", exact: true })
    .click();
  await expect(peCard.getByRole("checkbox")).not.toBeChecked();
  await peCard.getByRole("checkbox").focus();
  await page.keyboard.press("Space");
  await expect(peCard.getByRole("checkbox")).toBeChecked();
  assert.deepEqual(await selectedIds(), selection);
  checks.push({ name: "explicit_remove_and_keyboard_reselect", passed: true });
  await page.getByLabel("独立采集标的", { exact: true }).fill("601288.SH");
  await page.getByLabel("独立采集开始日期", { exact: true }).fill("2025-09-01");
  const update = page.getByRole("button", {
    name: "更新目录状态",
    exact: true,
  });
  await update.focus();
  const refreshed = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname === "/workspace/data" &&
      r.request().method() === "POST" &&
      Boolean(r.request().headers()["next-action"]),
    { timeout: 30000 },
  );
  await page.keyboard.press("Enter");
  const refreshedResponse = await refreshed;
  assert.equal(refreshedResponse.status(), 200);
  // Check the completed business state and its scoped snapshot identity. A
  // streaming response observer can remain pending after the action resolves.
  await expect(update).toBeEnabled();
  await expect(status).toContainText("目录状态已更新");
  const latestResponse = await page.request.get(`${root}/factor-catalog`);
  assert.equal(latestResponse.status(), 200);
  const latestSnapshot = await latestResponse.json();
  await expect(status).toHaveAttribute(
    "data-factor-catalog-sha256",
    latestSnapshot.content_sha256,
  );
  assert.deepEqual(await selectedIds(), selection);
  await expect(page.getByLabel("独立采集标的", { exact: true })).toHaveValue(
    "601288.SH",
  );
  await expect(
    page.getByLabel("独立采集开始日期", { exact: true }),
  ).toHaveValue("2025-09-01");
  checks.push({
    name: "catalog_status_keyboard_refresh_preserves_current_inputs",
    passed: true,
  });
  await page.reload({ timeout: 90000 });
  await page
    .getByLabel("采集数据类别", { exact: true })
    .selectOption("factors");
  await expect(page.getByLabel("因子目录状态", { exact: true })).toContainText(
    "历史权限与范围以采集响应为准",
  );
  checks.push({ name: "catalog_page_reload", passed: true });
  await page.getByLabel("搜索因子", { exact: true }).fill("pe_ttm");
  await page
    .locator('[data-factor-id="pe_ttm"]')
    .getByText("查看定义与元数据", { exact: true })
    .click();
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
    await page.getByLabel("搜索因子", { exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({
      path: `../../.tools/verification-local/review/factor-picker-${width}.png`,
    });
    checks.push({
      name: `catalog_layout_accessibility_${width}`,
      passed: true,
    });
  }
  const anonymous = await browser.newContext();
  assert.equal(
    (await anonymous.request.get(`${root}/factor-catalog`)).status(),
    401,
  );
  await anonymous.close();
  checks.push({ name: "anonymous_catalog_rejected", passed: true });
  writeFileSync(
    "../../.tools/verification-local/review/factor-catalog-ui-results.json",
    JSON.stringify(
      {
        checks,
        limitation:
          "Persisted real provider metadata; this run creates no download and does not prove current vendor rights",
      },
      null,
      2,
    ),
    { mode: 0o600 },
  );
  console.log(JSON.stringify({ checks }));
} finally {
  await browser.close();
}
