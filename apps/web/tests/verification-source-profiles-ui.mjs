import { openDataView } from "./data-workbench-navigation.mjs";
/** Actual authenticated capability evidence UI, without any supplier probes.
 * Controlled transport failure is separate from supplier availability evidence.
 * No price requests, enabled schedules, or fabricated HTTP responses are used.
 */
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const read = (path) => JSON.parse(readFileSync(`../../.tools/${path}`));
const baseline = read("verification-local/review/collection-retest-ui.json");
const admin = read("sso-local/state.json");
const reader = read("verification-local/approver.json");
const origin = "http://127.0.0.1:13000";
const root = `http://127.0.0.1:3122/api/v1/workspaces/${baseline.scope_id}/data/collector`;
const endpoint = `${root}/source-profiles`;
const referenceEndpoint =
  root.replace("/collector", "/universes") + "/source-profiles";
const browser = await chromium.launch();
const checks = [];
let expectedCatalog;
let phase = "initialization";
let collectorStopped = false;
const collectorCommand = (...args) =>
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
      ...args,
    ],
    { cwd: "../..", stdio: "pipe", timeout: 90000 },
  );
try {
  for (const role of ["admin", "reader"]) {
    const context = await browser.newContext({
      viewport:
        role === "admin"
          ? { width: 1440, height: 1000 }
          : { width: 390, height: 844 },
    });
    const page = await context.newPage();
    page.setDefaultTimeout(30000);
    phase = `${role} login`;
    console.log(`Checking ${role} source-profile UI`);
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
    await page.goto(`${origin}/workspace/data`);
    await openDataView(page, "来源与新鲜度", "来源能力");

    const response = await page.request.get(endpoint);
    phase = `${role} catalog and UI`;
    assert.equal(response.status(), 200);
    assert.equal(response.headers()["cache-control"], "no-store");
    const catalog = await response.json();
    assert.equal(catalog.scope_id, baseline.scope_id);
    const referenceResponse = await page.request.get(referenceEndpoint);
    assert.equal(referenceResponse.status(), 200);
    const referenceCatalog = await referenceResponse.json();
    assert.equal(referenceCatalog.scope_id, catalog.scope_id);
    assert.equal(referenceCatalog.profiles.length, 1);
    assert.equal(referenceCatalog.profiles[0].source, "mairui.reference");
    assert.equal(referenceCatalog.profiles[0].kind, "current_membership");
    catalog.profiles.push(...referenceCatalog.profiles);
    assert.equal(catalog.profiles.length, 11);
    // Compare the seven registered entries against their existing public
    // discovery contracts, not merely the new UI's own profile endpoint.
    const registered = [
      ...(await (await page.request.get(`${root}/catalog`)).json()),
      ...(await (await page.request.get(`${root}/research/catalog`)).json()),
    ];
    assert.equal(registered.length, 7);
    for (const entry of registered) {
      const profile = catalog.profiles.find((p) => p.source === entry.source);
      assert(profile);
      assert.equal(profile.mapping_version, entry.mapping_version);
      assert.equal(profile.kind, entry.dataset_kind ?? entry.kind);
      assert.equal(
        profile.state,
        entry.configured ? "configured" : "unconfigured",
      );
      assert.deepEqual(profile.markets, entry.markets);
      assert.deepEqual(profile.instrument_kinds, entry.instrument_kinds);
      assert.deepEqual(profile.periods, entry.periods ?? []);
    }
    if (role === "admin") expectedCatalog = catalog;
    else assert.deepEqual(catalog, expectedCatalog);
    const panel = page.locator("[data-source-profiles]");
    await panel.locator(":scope > summary").focus();
    await page.keyboard.press("Enter");
    await expect(panel.getByRole("status")).toHaveText(
      "已读取 11 项来源能力说明",
      { timeout: 60000 },
    );
    const selection = panel.getByLabel("查看数据能力", { exact: true });
    for (const profile of catalog.profiles) {
      phase = `${role} profile ${profile.source}`;
      await selection.selectOption(profile.source);
      const section = panel.getByRole("region", {
        name: "所选来源证据",
        exact: true,
      });
      await expect(
        section.getByRole("heading", { name: profile.name, exact: true }),
      ).toBeVisible();
      for (const fact of profile.facts) {
        await expect(section).toContainText(fact.detail);
        await expect(section).toContainText(`核对截至 ${fact.as_of}`);
        if (fact.unblock_condition)
          await expect(section).toContainText(fact.unblock_condition);
      }
      if (profile.state === "not_connected")
        await expect(section).toContainText("尚未接通，不能创建采集任务");
      if (profile.kind === "current_membership")
        await expect(section).toContainText("当前快照，不支持历史日期查询");
      assert.equal(profile.facts.length, 8);
      await expect(
        panel.getByRole("button", { name: /下载|预览|启用/ }),
      ).toHaveCount(0);
    }
    await selection.selectOption("mairui.factor.history");
    await expect(panel).toContainText("元数据缺单位、频率和计算版本");
    await expect(panel).toContainText("平台保护预算，不是套餐剩余权益");
    // Only abort the deliberate catalog refresh. This verifies a transport
    // failure without inventing an upstream permission or availability result.
    await page.route("**/workspace/data*", async (route) => {
      if (
        route.request().method() === "POST" &&
        route.request().headers()["next-action"]
      )
        await route.abort("failed");
      else await route.continue();
    });
    await panel
      .getByRole("button", { name: "刷新来源说明", exact: true })
      .click();
    await expect(panel.getByRole("alert")).toContainText(
      "保留的旧说明仅供参考",
    );
    await expect(
      panel.getByRole("button", { name: "刷新来源说明", exact: true }),
    ).toBeEnabled();
    await page.unroute("**/workspace/data*");
    await panel
      .getByRole("button", { name: "刷新来源说明", exact: true })
      .click();
    await expect(panel.getByRole("alert")).toHaveCount(0);
    await expect(panel.getByRole("status")).toHaveText(
      "已读取 11 项来源能力说明",
    );
    const freshCollection = await (await page.request.get(endpoint)).json();
    const freshReference = await (
      await page.request.get(referenceEndpoint)
    ).json();
    assert.deepEqual(
      [...freshCollection.profiles, ...freshReference.profiles],
      catalog.profiles,
    );
    if (role === "admin") {
      phase = "unconnected capability rejection";
      const session = await (
        await page.request.get("http://127.0.0.1:3122/auth/session")
      ).json();
      const beforeJobs = await (
        await page.request.get(`${root}/research/jobs`)
      ).json();
      for (const profile of catalog.profiles.filter(
        (p) => p.state === "not_connected",
      )) {
        const rejected = await page.request.post(`${root}/research/preview`, {
          headers: { "X-CSRF-Token": session.csrf_token },
          data: {
            source: profile.source,
            kind: profile.kind,
            instrument_ids: ["601288.SH"],
            date_axis: "effective_date",
            start: "2025-01-01",
            end: "2025-02-01",
            mode: "missing",
          },
        });
        assert.equal(rejected.status(), 400);
        assert.equal((await rejected.json()).detail.code, "unsupported");
      }
      assert.deepEqual(
        await (await page.request.get(`${root}/research/jobs`)).json(),
        beforeJobs,
      );
      checks.push(
        "three unconnected extension previews rejected unsupported without creating research jobs",
      );
    }
    if (
      role === "admin" &&
      process.env.QTRADE_VERIFY_PROFILE_OUTAGE === "true"
    ) {
      phase = "controlled collector stop and partial UI";
      // Only the existing verification collector; preserve all persistent
      // volumes and restore it even if a partial-read assertion fails.
      collectorStopped = true;
      collectorCommand("stop", "--timeout", "20", "collector");
      await panel
        .getByRole("button", { name: "刷新来源说明", exact: true })
        .click();
      await expect(panel.getByRole("status")).toHaveText(
        "已读取 1 项来源能力说明",
      );
      await expect(panel.getByRole("alert")).toContainText(
        "行情与研究采集说明",
      );
      await expect(panel).toContainText("当前仅展示已读取部分");
      await expect(
        panel.getByRole("heading", {
          name: "MaiRui 当前板块与指数成员",
          exact: true,
        }),
      ).toBeVisible();
      assert.equal((await page.request.get(referenceEndpoint)).status(), 200);
      collectorCommand("start", "--wait", "collector");
      collectorStopped = false;
      await panel
        .getByRole("button", { name: "刷新来源说明", exact: true })
        .click();
      await expect(panel.getByRole("status")).toHaveText(
        "已读取 11 项来源能力说明",
      );
      await expect(panel.getByRole("alert")).toHaveCount(0);
      checks.push(
        "actual collector stop: current members remained readable, missing collection group explicit; original container started and complete catalog recovered",
      );
    }
    await page.reload();
    await panel.locator(":scope > summary").click();
    await expect(panel.getByRole("status")).toHaveText(
      "已读取 11 项来源能力说明",
    );
    await selection.selectOption("mairui.financial.income");
    await expect(panel).toContainText("无证据as-of拒绝");
    assert.equal(
      (
        await new AxeBuilder({ page })
          .include("[data-source-profiles]")
          .analyze()
      ).violations.length,
      0,
    );
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
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
    await panel.evaluate((node) => node.scrollIntoView({ block: "start" }));
    await page.screenshot({
      path: `../../.tools/verification-local/review/source-profiles-${role}.png`,
    });
    checks.push(
      `${role}: all 11 public profiles/8 facts match UI, unknown conditions and disconnected limitations, keyboard, reload, fault/retry, axe0 and overflow passed`,
    );
    await context.close();
  }
  const anonymous = await browser.newContext();
  assert.equal((await anonymous.request.get(endpoint)).status(), 401);
  assert.equal((await anonymous.request.get(referenceEndpoint)).status(), 401);
  await anonymous.close();
  writeFileSync(
    "../../.tools/verification-local/review/source-profiles-ui-results.json",
    JSON.stringify(
      {
        checks,
        scope_id: baseline.scope_id,
        profiles: expectedCatalog,
        supplier_probe: false,
        controlled_transport_failure: true,
        actual_collector_outage:
          process.env.QTRADE_VERIFY_PROFILE_OUTAGE === "true",
      },
      null,
      2,
    ),
    { mode: 0o600 },
  );
  console.log("PASS source capability evidence desktop/mobile deployed UI");
} catch (error) {
  // Playwright errors can contain OAuth callback query strings or form values.
  // Report only the owned phase/type; never print the original error or URL.
  console.error(`FAIL source profiles at ${phase}: ${error?.name ?? "Error"}`);
  if (!phase.includes("login")) {
    for (const context of browser.contexts())
      for (const page of context.pages()) {
        const panel = page.locator("[data-source-profiles]");
        if (await panel.isVisible().catch(() => false)) {
          // Only this read-only evidence panel, never URLs, sessions or forms.
          writeFileSync(
            "../../.tools/verification-local/review/source-profile-failure.json",
            JSON.stringify({
              phase,
              text: await panel.innerText({ timeout: 1000 }),
            }),
            { mode: 0o600 },
          );
        }
      }
  }
  process.exitCode = 1;
} finally {
  if (collectorStopped) collectorCommand("start", "--wait", "collector");
  await browser.close();
}
