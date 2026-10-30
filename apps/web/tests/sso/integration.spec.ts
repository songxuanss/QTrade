import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";

const state = JSON.parse(
  readFileSync("../../.tools/sso-local/state.json", "utf8"),
);
test("real SSO login, callback and persisted QTrade session", async ({
  page,
}) => {
  page.on("console", (message) => {
    if (message.text().includes("form-action"))
      console.log("SSO CSP blocked form navigation");
  });
  await page.goto("/workspace/strategies");
  await expect(page).toHaveURL(/127\.0\.0\.1:3121\/auth\/login/);
  await page
    .getByRole("textbox", { name: "邮箱", exact: true })
    .fill("qtrade-integration@example.test");
  await page.getByLabel("密码", { exact: true }).fill(state.password);
  await page.getByRole("button", { name: "登 录", exact: true }).click();
  await expect(page).toHaveURL(
    /(?:3121\/auth\/consent|3120\/workspace\/strategies)/,
  );
  if (new URL(page.url()).pathname === "/auth/consent")
    await page.getByRole("button", { name: "同意授权", exact: true }).click();
  await expect(page).toHaveURL("http://127.0.0.1:3120/workspace/strategies");
  await expect(page.getByText("身份已验证", { exact: true })).toBeVisible();
  const before = await (await page.request.get("/auth/session")).json();
  expect(before.authenticated).toBe(true);
  await page.reload();
  const after = await (await page.request.get("/auth/session")).json();
  expect(after.user_id).toBe(before.user_id);
  expect(after.workspace_id).toBe(before.workspace_id);
  const cookies = await page.context().cookies("http://127.0.0.1:3120");
  expect(
    cookies.find((cookie) => cookie.name === "qtrade-session")?.httpOnly,
  ).toBe(true);
  expect(await page.evaluate(() => document.cookie)).not.toContain(
    "qtrade-session",
  );
  const other = await page.context().newPage();
  await other.goto("http://127.0.0.1:3120/workspace/data");
  await expect(other.getByText("身份已验证", { exact: true })).toBeVisible();
  const ssoCookies = await page.context().cookies("http://127.0.0.1:3121");
  const csrf = ssoCookies.find((cookie) => cookie.name === "sso_csrf")?.value;
  expect(Boolean(csrf)).toBe(true);
  const logout = await page.request.post("http://127.0.0.1:3121/auth/logout", {
    form: { csrf_token: csrf! },
    maxRedirects: 0,
  });
  expect(logout.status()).toBe(303);
  await expect
    .poll(async () => (await page.request.get("/auth/session")).status(), {
      timeout: 15_000,
    })
    .toBe(401);
  expect(
    (await other.request.get("http://127.0.0.1:3120/auth/session")).status(),
  ).toBe(401);
  await other.close();
  await page.reload();
  await expect(page).toHaveURL(/127\.0\.0\.1:3121\/auth\/login/);
});
