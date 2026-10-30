import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
const identity = JSON.parse(readFileSync("../../.tools/sso-local/state.json"));
const origin = "http://127.0.0.1:3120",
  api = "http://127.0.0.1:3122";
const browser = await chromium.launch();
try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  const page = await context.newPage();
  await page.goto(`${origin}/workspace/data`, { timeout: 120000 });
  await page
    .getByRole("textbox", { name: "邮箱", exact: true })
    .fill("qtrade-integration@example.test");
  await page.getByLabel("密码", { exact: true }).fill(identity.password);
  await page.getByRole("button", { name: "登 录", exact: true }).click();
  await page.waitForURL(/3120\/workspace\/data|3121\/auth\/consent/, {
    timeout: 120000,
  });
  if (page.url().includes("/auth/consent"))
    await page.getByRole("button", { name: "同意授权", exact: true }).click();
  await expect(page).toHaveURL(`${origin}/workspace/data`, { timeout: 120000 });
  const session = await (await page.request.get(`${api}/auth/session`)).json();
  const root = `${api}/api/v1/workspaces/${session.workspace_id}/data`;
  await page.goto(
    `${origin}/workspace/data?universe_search=${encodeURIComponent("国有大型银行")}&universe_category=industry`,
  );
  await page
    .locator('form:has(input[value="mairui:sw2_480200"])')
    .getByRole("button", { name: "预览并保存成员", exact: true })
    .click();
  await page.waitForURL(/universe_snapshot=/, { timeout: 120000 });
  const snapshotId = new URL(page.url()).searchParams.get("universe_snapshot");
  const snapshot = await (
    await page.request.get(`${root}/universes/snapshots/${snapshotId}`)
  ).json();
  assert.ok(snapshot.members.length >= 5 && snapshot.members.length <= 10);
  console.log(`Actual industry: ${snapshot.members.length} members`);
  async function configure(mode) {
    await page
      .getByLabel("板块下载开始日期", { exact: true })
      .fill("2025-09-01");
    await page
      .getByLabel("板块下载结束日期", { exact: true })
      .fill("2025-09-10");
    await page.getByLabel("板块下载方式", { exact: true }).selectOption(mode);
  }
  async function preview() {
    await page
      .getByRole("button", { name: "预览全部成员下载任务", exact: true })
      .click();
    await expect(page.locator(".campaign-preview")).toBeVisible({
      timeout: 120000,
    });
  }
  async function download() {
    await page
      .getByRole("button", { name: "一键下载所有成员", exact: true })
      .click();
    await expect(page.locator(".campaign-progress")).toBeVisible({
      timeout: 120000,
    });
    return page.locator(".campaign-progress").getAttribute("data-campaign-id");
  }
  async function complete(id) {
    const deadline = Date.now() + 900000;
    let run;
    while (Date.now() < deadline) {
      const response = await page.request.get(`${root}/campaigns/${id}`);
      assert.equal(response.status(), 200);
      run = await response.json();
      if (run.state === "completed") break;
      assert.equal(
        run.failed_parts,
        0,
        `batch failures: ${JSON.stringify(run.members.filter((m) => m.state === "failed"))}`,
      );
      await page.waitForTimeout(1000);
    }
    assert.equal(
      run.state,
      "completed",
      "actual entire board did not complete",
    );
    assert.equal(run.viewable_members, run.member_count);
    assert.equal(run.expanded_parts, run.planned_parts);
    assert.ok(
      run.members.every(
        (m) =>
          m.viewable &&
          m.parts.every((p) => p.raw_records > 0 && p.state === "viewable"),
      ),
    );
    return run;
  }
  await configure("all");
  await preview();
  await expect(page.locator(".campaign-preview")).toContainText(
    `${snapshot.members.length} 只股票`,
  );
  const firstId = await download();
  const first = await complete(firstId);
  assert.equal(first.planned_parts, snapshot.members.length * 2);
  const tasks = await (
    await page.request.get(`${root}/tasks?limit=200`)
  ).json();
  for (const member of first.members) {
    const actual = tasks.filter((task) =>
      member.parts.some((part) => part.task_id === task.task_id),
    );
    assert.equal(actual.length, 2);
    assert.ok(
      actual.every(
        (task) => Date.parse(task.end) - Date.parse(task.start) <= 7 * 86400000,
      ),
    );
  }
  const batches = await (
    await page.request.get(`${root}/batches?limit=200`)
  ).json();
  for (const member of first.members)
    for (const part of member.parts) {
      assert.ok(
        batches.some(
          (batch) =>
            batch.ingestion_task_id === part.task_id && batch.record_count > 0,
        ),
      );
    }
  await page.reload();
  await expect(page.locator(".campaign-progress")).toContainText(
    `可查看 ${snapshot.members.length}/${snapshot.members.length}`,
    { timeout: 60000 },
  );
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    assert.deepEqual((await new AxeBuilder({ page }).analyze()).violations, []);
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    await page.locator("#pool-download").screenshot({
      path: `../../.tools/verification-local/review/campaign-${width}.png`,
    });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByText("创建我的股票池", { exact: true }).click();
  await page.getByLabel("股票池名称", { exact: true }).fill("批量重叠验收");
  await page
    .getByLabel("股票代码", { exact: true })
    .fill("601288.SH,600519.SH,601288");
  await page
    .getByRole("button", { name: "保存自定义股票池", exact: true })
    .click();
  await page.waitForURL(/universe_snapshot=/, { timeout: 120000 });
  await expect(page.locator("#universe-members")).toContainText("2 个成员");
  await page
    .locator(".campaign-pools")
    .getByRole("checkbox", { name: new RegExp("国有大型银行") })
    .check();
  await configure("missing");
  await preview();
  await expect(page.locator(".campaign-preview")).toContainText(
    `${snapshot.members.length + 1} 只股票`,
  );
  const secondId = await download();
  const second = await complete(secondId);
  assert.equal(second.member_count, snapshot.members.length + 1);
  assert.equal(
    new Set(second.members.map((m) => m.instrument_id)).size,
    second.member_count,
  );
  await page.reload();
  await expect(page.locator(".campaign-progress")).toContainText(
    `可查看 ${second.member_count}/${second.member_count}`,
    { timeout: 60000 },
  );
  // All data now present: UI preview explicitly reports no new gaps.
  await page
    .locator(".campaign-pools")
    .getByRole("checkbox", { name: new RegExp("国有大型银行") })
    .check();
  await configure("missing");
  await preview();
  await expect(page.locator(".campaign-preview")).toContainText("新建 0 个");
  const anon = await browser.newContext();
  assert.equal(
    (await anon.request.get(`${root}/campaigns/${firstId}`)).status(),
    401,
  );
  await anon.close();
  assert.equal(
    (
      await page.request.get(
        `${api}/api/v1/workspaces/00000000-0000-4000-8000-000000000001/data/campaigns/${firstId}`,
      )
    ).status(),
    404,
  );
  const unsupported = await page.request.post(`${root}/campaigns/preview`, {
    headers: { "X-CSRF-Token": session.csrf_token },
    data: {
      snapshot_ids: [snapshotId],
      granularity: "1m",
      start: "2025-09-01T00:00:00Z",
      end: "2025-09-02T00:00:00Z",
    },
  });
  assert.equal(unsupported.status(), 400);
  await page.goto(
    `${origin}/workspace/data?universe_search=${encodeURIComponent("银行")}&universe_category=industry`,
  );
  await page
    .locator('form:has(input[value="mairui:sw_yx"])')
    .getByRole("button", { name: "下载全部成员", exact: true })
    .click();
  await page.waitForURL(/universe_snapshot=.+#pool-download/, {
    timeout: 120000,
  });
  const largeSnapshotId = new URL(page.url()).searchParams.get(
    "universe_snapshot",
  );
  const largeSnapshot = await (
    await page.request.get(`${root}/universes/snapshots/${largeSnapshotId}`)
  ).json();
  assert.ok(
    largeSnapshot.members.length > 30,
    "large industry does not exercise pagination",
  );
  await configure("all");
  await preview();
  const largeId = await download();
  const expansionDeadline = Date.now() + 30000;
  let partial;
  while (Date.now() < expansionDeadline) {
    partial = await (
      await page.request.get(`${root}/campaigns/${largeId}`)
    ).json();
    if (
      partial.expanded_parts > 0 &&
      partial.expanded_parts < partial.planned_parts
    )
      break;
    await page.waitForTimeout(100);
  }
  assert.ok(
    partial.expanded_parts > 0 &&
      partial.expanded_parts < partial.planned_parts,
    "did not observe durable partial expansion",
  );
  execFileSync(
    "../../.venv/bin/python",
    ["../../scripts/verification-local.py", "stop"],
    { stdio: "pipe" },
  );
  execFileSync(
    "../../.venv/bin/python",
    ["../../scripts/verification-local.py", "start"],
    { stdio: "pipe" },
  );
  const large = await complete(largeId);
  assert.equal(large.planned_parts, large.member_count * 2);
  const lastPage = await (
    await page.request.get(`${root}/campaigns/${largeId}?member_offset=30`)
  ).json();
  assert.equal(lastPage.members.length, large.member_count - 30);
  assert.ok(
    lastPage.members.every(
      (member) =>
        member.viewable && member.parts.every((part) => part.raw_records > 0),
    ),
  );
  assert.equal(
    new Set(
      [...large.members, ...lastPage.members].flatMap((member) =>
        member.parts.map((part) => part.task_id),
      ),
    ).size,
    large.planned_parts,
  );
  await page.reload();
  await expect(page.locator(".campaign-progress")).toContainText(
    `可查看 ${large.member_count}/${large.member_count}`,
    { timeout: 60000 },
  );
  await page
    .locator('[aria-label="下载成员分页"]')
    .getByRole("button", { name: "下一页", exact: true })
    .click();
  await expect(page.locator('[aria-label="下载成员分页"]')).toContainText(
    "第 2 页",
  );
  await expect(
    page.getByRole("button", { name: "预览全部成员下载任务", exact: true }),
  ).toBeEnabled();
  console.log(
    `Large industry ${large.member_count}: restart, stable work IDs and paging PASS`,
  );
  await page.goto(`${origin}/workspace/market?instrument=601288.SH`);
  await expect(page.locator(".market-chart-legend")).toContainText("根 Bar", {
    timeout: 60000,
  });
  writeFileSync(
    "../../.tools/verification-local/review/campaign-results.txt",
    `PASS: real industry ALL ${first.member_count} stocks -> UI preview -> 7-day pieces -> durable campaign -> actual MaiRui -> Worker -> nonempty verified archives -> all published/viewable -> refresh -> desktop/mobile Axe/visual -> overlapping custom+industry dedup ${second.member_count} stocks -> missing-only -> no new gaps -> large industry full collection/restart/page2/stable IDs -> real daily market chart -> anonymous/cross-scope/unauthorized period rejection.\n`,
  );
  console.log("Entire-board collection and overlapping pools: PASS");
} finally {
  await browser.close();
}
