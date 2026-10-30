import assert from "node:assert/strict";
import { readFileSync, writeFileSync, chmodSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const identity = JSON.parse(readFileSync("../../.tools/sso-local/state.json"));
const scope = JSON.parse(
  readFileSync("../../.tools/verification-local/budget-scope.json"),
).scope_id;
const browser = await chromium.launch();
const instrument = process.env.QTRADE_SCHEDULE_TEST_INSTRUMENT ?? "600519.SH";
assert(["600519.SH", "600036.SH"].includes(instrument));
const stockName = instrument === "600519.SH" ? "贵州茅台" : "招商银行";
const checks = [];
let page, session, root, saved;
try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  page = await context.newPage();
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
  await expect(page).toHaveURL("http://127.0.0.1:13000/workspace/data", {
    timeout: 90000,
  });
  await page.getByLabel("切换工作区", { exact: true }).selectOption(scope);
  await page.getByRole("button", { name: "切换", exact: true }).click();
  await expect(page).toHaveURL("http://127.0.0.1:13000/", { timeout: 60000 });
  await page.goto("http://127.0.0.1:13000/workspace/data", { timeout: 90000 });
  session = await (
    await page.request.get("http://127.0.0.1:3122/auth/session")
  ).json();
  root = `http://127.0.0.1:3122/api/v1/workspaces/${scope}/data/collector`;
  const panel = page.getByRole("region", { name: "自动更新计划", exact: true });
  await panel.locator("summary").focus();
  await page.keyboard.press("Enter");
  const name = `${stockName} UI 定时验证 ${Date.now()}`;
  const instant = new Date(Date.now() + 120000);
  const local = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(instant);
  await panel.getByLabel("自动计划名称", { exact: true }).fill(name);
  await panel.getByLabel("自动计划标的", { exact: true }).fill(instrument);
  await panel.getByLabel("自动计划运行时间", { exact: true }).fill(local);
  await panel.getByLabel("自动计划回看天数", { exact: true }).fill("7");
  await panel.getByLabel("自动计划分段上限", { exact: true }).fill("20");
  await expect(
    panel.getByRole("button", { name: "保存停用计划", exact: true }),
  ).toBeDisabled();
  await panel.getByLabel("确认自动计划授权", { exact: true }).check();
  await panel
    .getByRole("button", { name: "保存停用计划", exact: true })
    .focus();
  await page.keyboard.press("Enter");
  const card = panel.locator(".schedule-card").filter({ hasText: name });
  await expect(card).toContainText("已停用", { timeout: 90000 });
  const id = await card.getAttribute("data-schedule-id");
  saved = (await (await page.request.get(root + "/schedules")).json()).find(
    (r) => r.draft.schedule_id === id,
  );
  assert(saved && saved.state === "disabled");
  await page.reload({ timeout: 90000 });
  await expect(card).toContainText("已停用");
  await card.getByRole("button", { name: "启用自动计划", exact: true }).click();
  await expect(card).toContainText("已启用", { timeout: 90000 });
  checks.push(
    "keyboard UI creation, explicit acknowledgement, disabled persistence/reload and enable",
  );
  assert.equal(
    (
      await page.request.post(`${root}/schedules/${id}/disable`, {
        data: { revision: saved.revision },
      })
    ).status(),
    403,
  );
  assert.equal(
    (
      await page.request.post(`${root}/schedules/${id}/enable`, {
        headers: { "X-CSRF-Token": session.csrf_token },
        data: { revision: "b".repeat(64) },
      })
    ).status(),
    409,
  );
  assert.equal(
    (
      await page.request.get(
        `http://127.0.0.1:3122/api/v1/workspaces/00000000-0000-0000-0000-000000000001/data/collector/schedules`,
      )
    ).status(),
    404,
  );
  checks.push("CSRF, stale revision and foreign scope rejection");
  let job;
  await expect
    .poll(
      async () => {
        const response = await page.request.get(root + "/jobs");
        assert.equal(response.status(), 200);
        const jobs = await response.json();
        const candidates = jobs.filter(
          (j) =>
            j.request.instrument_ids.includes(instrument) &&
            j.request.granularity === "1d",
        );
        job = candidates.find(
          (j) => Date.parse(j.created_at) >= Date.parse(saved.updated_at),
        );
        return (
          job?.state === "completed" &&
          job.parts.every((p) => p.consumer?.state === "visible")
        );
      },
      { timeout: 240000, intervals: [1000, 2000, 3000] },
    )
    .toBe(true);
  assert(job.parts.some((p) => p.publication.record_count > 0));
  await panel
    .getByRole("button", { name: "刷新自动计划", exact: true })
    .click();
  await card.getByRole("button", { name: "停用自动计划", exact: true }).click();
  await expect(card).toContainText("已停用", { timeout: 90000 });
  await page.reload({ timeout: 90000 });
  await expect(card).toContainText("已停用");
  checks.push(
    "deployed timer/Worker/source/nonempty publication and UI disable persistence",
  );
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    assert.equal(
      (await new AxeBuilder({ page }).include(".schedule-panel").analyze())
        .violations.length,
      0,
    );
    await panel.screenshot({
      path: `../../.tools/verification-local/review/schedule-controls-${width}.png`,
    });
  }
  const result = {
    checks,
    schedule_id: id,
    job_id: job.job_id,
    scope_id: scope,
    rows: job.parts.reduce((n, p) => n + (p.publication?.record_count ?? 0), 0),
  };
  const file =
    "../../.tools/verification-local/review/schedule-controls-ui.json";
  writeFileSync(file, JSON.stringify(result, null, 2));
  chmodSync(file, 0o600);
  console.log(
    `PASS schedule controls UI: ${checks.length} checks, ${result.rows} rows`,
  );
} finally {
  if (page && saved && session) {
    const disabled = await page.request.post(
      `${root}/schedules/${saved.draft.schedule_id}/disable`,
      {
        headers: { "X-CSRF-Token": session.csrf_token },
        data: { revision: saved.revision },
      },
    );
    assert.equal(
      disabled.status(),
      200,
      "owned verification plan must be disabled",
    );
  }
  await browser.close();
}
