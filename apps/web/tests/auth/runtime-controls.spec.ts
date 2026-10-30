import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("trading runtime requires a typed confirmation before stopping", async ({
  page,
}) => {
  await page.goto("/workspace/strategies");
  await expect(page.getByText("身份已验证", { exact: true })).toBeVisible();
  const session = await (await page.request.get("/auth/session")).json();
  const workspaceId = session.workspace_id as string;

  await page.goto("/workspace/trading");
  await expect(page.getByRole("heading", { name: "交易执行" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "策略版本" })).toBeVisible();
  await expect(page.getByText("实盘启动需审批")).toBeVisible();
  await expect(page.getByRole("button", { name: "暂停" })).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);

  const stop = page.getByRole("button", { name: "停止运行" });
  await stop.click();
  await expect(page).toHaveURL(/\/workspace\/trading$/);
  const confirmation = page.getByLabel(/确认停止运行/);
  await expect(confirmation).toBeFocused();
  await confirmation.fill("CONFIRM STOP");
  await stop.click();
  await expect(page).toHaveURL(/result=success/);
  await expect(page.getByText("已停止", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "恢复" })).toHaveCount(0);

  const state = await (
    await page.request.get("http://127.0.0.1:3111/fixture/runtime-controls", {
      params: { workspace_id: workspaceId },
    })
  ).json();
  expect(state.runtimes[0].state).toBe("stopped");
  expect(state.audit_actions).toEqual(["runtime.stop"]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
