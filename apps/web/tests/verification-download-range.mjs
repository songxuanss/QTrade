import assert from "node:assert/strict";
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const identity = JSON.parse(
  readFileSync("../../.tools/sso-local/state.json", "utf8"),
);
const origin = "http://127.0.0.1:3120";
const directory = "../../.tools/verification-local/review";
mkdirSync(directory, { recursive: true });
const browser = await chromium.launch();
try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${origin}/workspace/data?data_view=ticks`);
  await page
    .getByRole("textbox", { name: "邮箱", exact: true })
    .fill("qtrade-integration@example.test");
  await page.getByLabel("密码", { exact: true }).fill(identity.password);
  await page.getByRole("button", { name: "登 录", exact: true }).click();
  await expect(page).toHaveURL(/(?:3121\/auth\/consent|3120\/workspace\/data)/);
  if (new URL(page.url()).pathname === "/auth/consent") {
    await page.getByRole("button", { name: "同意授权", exact: true }).click();
  }
  await expect(page).toHaveURL(`${origin}/workspace/data`);
  await page.getByRole("tab", { name: "逐笔与兼容", exact: true }).click();
  const limited = process.argv.includes("--limit-check");
  await expect(
    page.getByText(limited ? "最多 7 天" : "下载时长不限", { exact: true }),
  ).toBeVisible();
  const originalRows = await page
    .locator("#collection .data-row")
    .allTextContents();
  await page
    .locator(".tick-request-form [name=instrument_id]")
    .fill("600519.SH");
  await page
    .locator(".tick-request-form [name=start]")
    .fill("2026-09-14T01:30");
  await page.locator(".tick-request-form [name=end]").fill("2026-09-22T07:00");
  await page.getByRole("button", { name: "创建 Tick 下载任务" }).click();
  if (limited) {
    await expect(page.locator(".data-notice[role=alert]")).toContainText(
      "下载范围超过当前配置上限",
    );
    assert.deepEqual(
      await page.locator("#collection .data-row").allTextContents(),
      originalRows,
    );
    await page.reload();
    await expect(page.getByText("最多 7 天", { exact: true })).toBeVisible();
    console.log(
      "Configured seven-day limit / actual UI rejection / no task creation / refresh: PASS",
    );
  } else {
    await page.waitForURL(/result=(?:complete|partial|success|error)/);
    if (new URL(page.url()).searchParams.get("result") === "complete") {
      await expect(
        page.getByRole("heading", { name: "该时段已完整下载" }),
      ).toBeVisible();
      await page
        .getByRole("button", { name: "重新下载全部", exact: true })
        .click();
    } else if (new URL(page.url()).searchParams.get("result") === "partial") {
      await page
        .getByRole("button", { name: "仅下载缺失数据", exact: true })
        .click();
    }
    await expect(page.locator(".data-notice[role=status]")).toContainText(
      "下载任务已提交",
    );
    const row = page.locator("#collection .data-row").first();
    await expect(row).toContainText("600519.SH");
    const session = await (
      await page.request.get("http://127.0.0.1:3122/auth/session")
    ).json();
    const taskUrl = `http://127.0.0.1:3122/api/v1/workspaces/${session.workspace_id}/data/tasks`;
    let tasks = await (await page.request.get(taskUrl)).json();
    const rowId = await row.getAttribute("data-task-id");
    const selected = tasks.find((task) => task.task_id === rowId);
    assert.ok(selected?.download_group_id);
    const parts = () =>
      tasks.filter(
        (task) => task.download_group_id === selected.download_group_id,
      );
    assert.equal(parts().length, 2);
    assert.ok(
      parts().every(
        (task) => Date.parse(task.end) - Date.parse(task.start) <= 7 * 86400000,
      ),
    );
    const deadline = Date.now() + 600_000;
    while (parts().some((task) => task.status !== "completed")) {
      assert.ok(
        Date.now() < deadline,
        `Download did not finish: ${await row.innerText()}`,
      );
      assert.ok(
        !(await row.locator(".data-status").innerText()).includes("失败"),
        `Download failed: ${await row.innerText()}`,
      );
      await page.waitForTimeout(1500);
      tasks = await (await page.request.get(taskUrl)).json();
      assert.ok(parts().every((task) => task.status !== "failed"));
      await page.getByRole("button", { name: "刷新数据", exact: true }).click();
      await expect(row).toBeVisible();
    }
    assert.ok(parts().every((task) => task.page_count > 0));
    await expect(row).toContainText(/[1-9]\d* 批次/);
    await expect(page.locator("#batches .data-row").first()).toContainText(
      /[1-9][\d,]* 条记录/,
    );
    await expect(page.locator("#batches .data-hash").first()).toContainText(
      "SHA-256",
    );
    await page.reload();
    await expect(row).toContainText("已完成");
    await expect(page.locator("#batches .data-row").first()).toBeVisible();
    for (const viewport of [
      { width: 1440, height: 1000 },
      { width: 390, height: 844 },
    ]) {
      await page.setViewportSize(viewport);
      assert.deepEqual(
        (await new AxeBuilder({ page }).analyze()).violations,
        [],
      );
      assert.ok(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      );
      await page.screenshot({
        path: `${directory}/data-${viewport.width}.png`,
        fullPage: true,
      });
    }
    assert.deepEqual(errors, []);
    writeFileSync(
      `${directory}/download-range-results.txt`,
      "Real SSO / UI unlimited range / more than seven days / task submission / live MaiRui / Worker / persisted batches / refresh / desktop-mobile accessibility: PASS\n",
    );
    console.log("Live A-share unlimited range download end-to-end: PASS");
  }
} finally {
  await browser.close();
}
