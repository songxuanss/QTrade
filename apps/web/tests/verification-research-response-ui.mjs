import { openDataView } from "./data-workbench-navigation.mjs";
/** Real response metadata UI. Controlled HTTP failures are not supplier incidents. */
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
const read = (path) => JSON.parse(readFileSync(`../../.tools/${path}`));
const admin = read("sso-local/state.json"),
  reader = read("verification-local/approver.json");
const partial = process.env.QTRADE_RESPONSE_PARTIAL === "1";
const suffix = partial ? "-partial" : "";
const fixtures = read(
  `verification-local/research-response${suffix}-fixtures.json`,
);
const financial = read(
  "verification-local/review/financial-ui-results.json",
).download;
const distribution = read(
  "verification-local/review/distribution-ui-results.json",
).download;
const scope = fixtures[0].scope_id;
assert.equal(financial.scope_id, scope);
assert.equal(distribution.scope_id, scope);
const targets = [
  ...fixtures.map((f) => ({
    ...f,
    expected: f.receipt_count,
    status: f.receipt.http_status,
    fixture: true,
  })),
  {
    ...financial,
    kind: "financial_statement",
    expected: 1,
    status: 200,
    fixture: false,
  },
  {
    ...distribution,
    kind: "corporate_action",
    expected: 1,
    status: 200,
    fixture: false,
  },
];
const origin = "http://127.0.0.1:13000";
const root = `http://127.0.0.1:3122/api/v1/workspaces/${scope}/data/collector/research`;
const browser = await chromium.launch();
const checks = [];
let phase = "login";
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
    console.log(`Checking ${role} response receipt UI`);
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
    await page.getByLabel("切换工作区", { exact: true }).selectOption(scope);
    await page.getByRole("button", { name: "切换", exact: true }).click();
    await expect(page).toHaveURL(`${origin}/`, { timeout: 60000 });
    await page.goto(`${origin}/workspace/data`);

    for (const target of targets) {
      await openDataView(
        page,
        "多维下载",
        target.kind === "financial_statement" ? "财务报表" : "分红、送转与配股",
      );
      phase = `${role} ${target.fixture ? "controlled" : "live"} ${target.kind} receipts`;
      const endpoint = `${root}/jobs/${target.request_id}/response-receipts`;
      const response = await page.request.get(endpoint + "?limit=20");
      assert.equal(response.status(), 200);
      assert.equal(response.headers()["cache-control"], "no-store");
      const first = await response.json();
      assert.equal(first.scope_id, scope);
      assert.equal(first.request_id, target.request_id);
      assert.equal(first.receipts.length, Math.min(20, target.expected));
      assert(
        first.receipts.every(
          (r) =>
            r.http_status === target.status &&
            (target.fixture
              ? r.body_bytes === target.receipt.body_bytes &&
                r.complete === target.receipt.complete
              : r.body_bytes > 0 && r.complete),
        ),
      );
      assert(
        first.receipts.every(
          (r) => !("body" in r) && !("url" in r) && !("headers" in r),
        ),
      );
      const panel = page.locator(
        `[data-response-receipts="${target.request_id}"]`,
      );
      await panel
        .getByRole("button", { name: "查看来源响应回执", exact: true })
        .focus();
      await page.keyboard.press("Enter");
      await expect(panel.getByRole("status")).toContainText("第 1 页", {
        timeout: 60000,
      });
      await expect(panel).toContainText(
        "不代表数据通过校验、已发布或可用于研究",
      );
      const ids = () =>
        panel
          .locator("[data-response-id]")
          .evaluateAll((nodes) => nodes.map((n) => n.dataset.responseId));
      assert.deepEqual(
        await ids(),
        first.receipts.map((r) => r.receipt_id),
      );
      if (first.next_id) {
        await panel
          .getByRole("button", { name: "下一页响应回执", exact: true })
          .click();
        await expect(panel.getByRole("status")).toContainText("第 2 页");
        const second = await (
          await page.request.get(endpoint + "?after_id=" + first.next_id)
        ).json();
        assert.deepEqual(
          await ids(),
          second.receipts.map((r) => r.receipt_id),
        );
        assert.equal(
          first.receipts.length + second.receipts.length,
          target.expected,
        );
        assert(!second.next_id);
        await panel
          .getByRole("button", { name: "上一页响应回执", exact: true })
          .click();
        await expect(panel.getByRole("status")).toContainText("第 1 页");
      }
      await panel
        .locator(
          `[data-response-id="${first.receipts[0].receipt_id}"] > summary`,
        )
        .click();
      await expect(panel).toContainText(first.receipts[0].body_sha256);
      await expect(panel).toContainText(
        first.receipts[0].complete
          ? "接收时HTTP响应正文完整"
          : "接收时响应正文不完整",
      );
      if (target.fixture && role === "admin") {
        await page.route("**/workspace/data*", async (route) => {
          if (
            route.request().method() === "POST" &&
            route.request().headers()["next-action"] &&
            route.request().postData()?.includes(target.request_id)
          )
            await route.abort("failed");
          else await route.continue();
        });
        await panel
          .getByRole("button", { name: "刷新响应回执", exact: true })
          .click();
        await expect(panel.getByRole("alert")).toContainText("旧记录仅供参考");
        await expect(
          panel.getByRole("button", { name: "刷新响应回执", exact: true }),
        ).toBeEnabled();
        await page.unroute("**/workspace/data*");
        await panel
          .getByRole("button", { name: "刷新响应回执", exact: true })
          .click();
        await expect(panel.getByRole("alert")).toHaveCount(0);
      }
      assert.equal(
        (
          await new AxeBuilder({ page })
            .include(`[data-response-receipts="${target.request_id}"]`)
            .analyze()
        ).violations.length,
        0,
      );
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      );
      for (const query of ["?limit=true", "?limit=101", "?after_id=bad"])
        assert.equal((await page.request.get(endpoint + query)).status(), 422);
      assert(
        [403, 404].includes(
          (
            await page.request.get(
              endpoint.replace(scope, "00000000-0000-4000-8000-000000000001"),
            )
          ).status(),
        ),
      );
      assert.equal((await page.request.get(endpoint + "/body")).status(), 404);
      const row = page.locator(`[data-research-job="${target.request_id}"]`);
      if (target.fixture) await expect(row).toContainText("失败");
      else await expect(row).toContainText("已发布");
      await page.reload();
      await panel
        .getByRole("button", { name: "查看来源响应回执", exact: true })
        .click();
      await expect(panel.getByRole("status")).toContainText("第 1 页");
      assert.deepEqual(
        await ids(),
        first.receipts.map((r) => r.receipt_id),
      );
      await panel.evaluate((node) => node.scrollIntoView({ block: "start" }));
      await page.screenshot({
        path: `../../.tools/verification-local/review/research-response${suffix}-${role}-${target.kind}-${target.fixture ? "fixture" : "live"}.png`,
      });
      await page.keyboard.press("Escape");
      await expect(panel.getByRole("dialog")).not.toBeVisible();
      checks.push(
        `${role}: ${target.fixture ? "controlled" : "live"} ${target.kind}, exact scoped IDs, immutable refresh, pagination, keyboard, axe0 and no overflow`,
      );
    }
    await context.close();
  }
  const anonymous = await browser.newContext();
  assert.equal(
    (
      await anonymous.request.get(
        `${root}/jobs/${targets[0].request_id}/response-receipts`,
      )
    ).status(),
    401,
  );
  await anonymous.close();
  writeFileSync(
    `../../.tools/verification-local/review/research-response${suffix}-ui-results.json`,
    JSON.stringify(
      { scope_id: scope, targets, checks, private_bodies_exposed: false },
      null,
      2,
    ),
    { mode: 0o600 },
  );
  console.log(
    "PASS research response receipt UI: admin/reader, live/controlled, paging and refresh",
  );
} catch (error) {
  console.error(
    `FAIL response receipt UI at ${phase}: ${error?.name ?? "Error"}`,
  );
  process.exitCode = 1;
} finally {
  await browser.close();
}
