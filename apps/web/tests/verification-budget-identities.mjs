import { chromium } from "@playwright/test";
import { readFileSync, writeFileSync, chmodSync } from "node:fs";
const browser = await chromium.launch();
const actors = {};
try {
  for (const [kind, identity] of [
    [
      "admin",
      {
        ...JSON.parse(readFileSync("../../.tools/sso-local/state.json")),
        email: "qtrade-integration@example.test",
      },
    ],
    [
      "reader",
      JSON.parse(readFileSync("../../.tools/verification-local/approver.json")),
    ],
  ]) {
    const context = await browser.newContext();
    const page = await context.newPage();
    await page.goto("http://127.0.0.1:13000/workspace/data", {
      timeout: 90000,
    });
    await page
      .getByRole("textbox", { name: "邮箱", exact: true })
      .fill(identity.email);
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
    const session = await (
      await page.request.get("http://127.0.0.1:3122/auth/session")
    ).json();
    if (!/^[a-f0-9-]{36}$/i.test(session.user_id))
      throw new Error("identity not established");
    actors[kind] = session.user_id;
    await context.close();
  }
  const file = "../../.tools/verification-local/budget-actors.json";
  writeFileSync(file, JSON.stringify(actors));
  chmodSync(file, 0o600);
  console.log("Actual SSO fixture identities recorded privately");
} finally {
  await browser.close();
}
