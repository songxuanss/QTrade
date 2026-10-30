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
  await page.goto(`${origin}/workspace/strategies`);
  await page
    .getByRole("textbox", { name: "邮箱", exact: true })
    .fill("qtrade-integration@example.test");
  await page.getByLabel("密码", { exact: true }).fill(identity.password);
  await page.getByRole("button", { name: "登 录", exact: true }).click();
  await expect(page).toHaveURL(
    /(?:3121\/auth\/consent|3120\/workspace\/strategies)/,
  );
  if (new URL(page.url()).pathname === "/auth/consent") {
    await page.getByRole("button", { name: "同意授权", exact: true }).click();
  }
  await expect(page).toHaveURL(`${origin}/workspace/strategies`);
  await expect(page.locator(".monaco-editor")).toBeVisible();
  const name = `专业界面验收 · 均线研究 ${new Date().toISOString().slice(11, 19)}`;
  await page.getByLabel("策略名称", { exact: true }).fill(name);
  await page
    .getByLabel("用途说明", { exact: true })
    .fill("验证策略保存、参数约束与不可变版本记录。");
  await page.getByLabel("参数 schema JSON").fill(
    JSON.stringify(
      {
        type: "object",
        properties: {
          lookback: { type: "integer", minimum: 2, maximum: 120, default: 20 },
        },
        required: ["lookback"],
        additionalProperties: false,
      },
      null,
      2,
    ),
  );
  await page.getByRole("button", { name: "创建策略", exact: true }).click();
  await expect(page).toHaveURL(/strategy_id=/);
  await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
  await page.getByLabel(/^lookback/).fill("1");
  await page.getByRole("button", { name: "校验参数", exact: true }).click();
  await expect(
    page.getByText("参数类型或取值范围不符合此版本 schema"),
  ).toBeVisible();
  await page.getByLabel(/^lookback/).fill("20");
  await page.getByRole("button", { name: "校验参数", exact: true }).click();
  await expect(page.getByRole("status")).toContainText(
    "参数符合此 Strategy Version",
  );
  await page.getByRole("button", { name: "保存为新版本", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("v2 已保存");
  await page.reload();
  await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
  await expect(page.locator(".strategy-version-list li")).toHaveCount(2);

  const results = [];
  for (const [label, path] of [
    ["strategies", page.url()],
    ["overview", `${origin}/`],
    ["backtests", `${origin}/workspace/backtests`],
    ["alerts", `${origin}/workspace/alerts`],
    ["trading", `${origin}/workspace/trading`],
    ["health", `${origin}/workspace/health`],
    ["market", `${origin}/workspace/market`],
    ["risk", `${origin}/workspace/risk`],
    ["data", `${origin}/workspace/data`],
    ["settings", `${origin}/workspace/settings`],
  ]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    if (label === "strategies")
      await expect(page.locator(".monaco-editor")).toBeVisible();
    for (const [viewport, size] of [
      ["desktop", { width: 1440, height: 1000 }],
      ["mobile", { width: 390, height: 844 }],
    ]) {
      await page.setViewportSize(size);
      assert.equal(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
        true,
        `${label}/${viewport}: horizontal overflow`,
      );
      const violations = (await new AxeBuilder({ page }).analyze()).violations;
      assert.deepEqual(
        violations,
        [],
        `${label}/${viewport}: accessibility failures`,
      );
      await page.screenshot({
        path: `${directory}/${label}-${viewport}.png`,
        fullPage: true,
      });
      results.push(`${label}/${viewport}: PASS`);
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
  }
  assert.deepEqual(errors, []);
  writeFileSync(
    `${directory}/results.txt`,
    [
      "Real SSO + actual PostgreSQL API + production Next.js",
      `Strategy: ${name}`,
      "UI create / invalid parameter rejection / valid parameter acceptance / v1+v2 persistence: PASS",
      ...results,
      "Browser runtime exceptions: none",
    ].join("\n") + "\n",
  );
  console.log(
    "Real deployment strategy workflow and desktop/mobile UI review: PASS",
  );
} finally {
  await browser.close();
}
