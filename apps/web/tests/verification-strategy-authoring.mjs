import assert from "node:assert/strict";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";

const identity = JSON.parse(
  readFileSync("../../.tools/sso-local/state.json", "utf8"),
);
const origin = "http://127.0.0.1:3120";
const directory = "../../.tools/verification-local/strategy-authoring";
mkdirSync(directory, { recursive: true });
const report = { steps: [], consoleErrors: [], viewports: {} };

function step(name, detail = {}) {
  report.steps.push({ name, at: new Date().toISOString(), ...detail });
  console.log(`· ${name}`);
}

const browser = await chromium.launch();
try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  const page = await context.newPage();
  page.on("pageerror", (error) =>
    report.consoleErrors.push(`pageerror: ${error.message}`),
  );
  page.on("console", (message) => {
    if (message.type() === "error") {
      report.consoleErrors.push(`console: ${message.text()}`);
    }
  });

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
  step("登录并打开策略工作台");

  const templatePanel = page.getByRole("region", { name: "策略模板" });
  await expect(templatePanel).toBeVisible();
  const cards = templatePanel.locator(".strategy-template-card");
  await expect(cards).toHaveCount(21);
  const groups = await templatePanel.locator(".strategy-template-group h3").allInnerTexts();
  assert.ok(groups.length >= 5, `template groups missing: ${groups}`);
  await expect(
    templatePanel.getByText("论坛实战派", { exact: false }),
  ).toBeVisible();
  step("模板面板可见", { count: 21, groups });

  const stamp = new Date().toISOString().slice(11, 19);
  const strategyName = `模板验收 · 横截面动量 ${stamp}`;
  await templatePanel
    .getByRole("button", { name: /横截面动量/ })
    .click();
  await expect(page.getByLabel("策略名称", { exact: true })).toHaveValue(
    "横截面动量",
  );
  await expect(page.locator(".strategy-schema-row")).toHaveCount(5);
  const appliedCode = await page.evaluate(
    () => window.monaco?.editor.getModels()[0]?.getValue() ?? "",
  );
  assert.ok(
    appliedCode.includes("class MomentumRank(Strategy)"),
    "template code was not applied to the editor",
  );
  assert.ok(appliedCode.includes("def on_bar"), "template code incomplete");
  step("选择模板后源码、名称与参数表单同步", {
    schemaRows: 5,
    codeChars: appliedCode.length,
  });

  await page.evaluate(() => {
    const monaco = window.monaco;
    const model = monaco.editor.getModels()[0];
    const editor = monaco.editor.getEditors()[0];
    window.__authoringOriginal = model.getValue();
    model.setValue(window.__authoringOriginal + "self.");
    editor.setPosition(model.getPositionAt(model.getValue().length));
    editor.focus();
    editor.trigger("verification", "editor.action.triggerSuggest", {});
  });
  await expect(page.locator(".suggest-widget.visible")).toBeVisible();
  const suggestions = await page
    .locator(".suggest-widget.visible")
    .innerText();
  assert.match(suggestions, /parameters/, `missing self.parameters: ${suggestions}`);
  assert.match(suggestions, /buy|order/, `missing order helpers: ${suggestions}`);
  await page.keyboard.press("Escape");
  await page.evaluate(() => {
    window.monaco.editor.getModels()[0].setValue(window.__authoringOriginal);
  });
  step("编辑器补全列出策略接口成员");

  const hovered = await page.evaluate(() => {
    const monaco = window.monaco;
    const editor = monaco.editor.getEditors()[0];
    const model = editor.getModel();
    const index = model.getValue().indexOf("self.buy(");
    editor.setPosition(model.getPositionAt(index + 5));
    editor.trigger("verification", "editor.action.showHover", {});
    return index;
  });
  assert.ok(hovered > 0, "buy call not found in template");
  await expect(page.locator(".monaco-hover").first()).toContainText("buy", {
    timeout: 10000,
  });
  step("悬停显示下单方法签名");

  const library = JSON.parse(
    readFileSync("src/features/strategies/templates.json", "utf8"),
  );
  const checked = [];
  for (const template of library) {
    await templatePanel
      .getByRole("button", { name: template.name })
      .click();
    const applied = await page.evaluate(
      () => window.monaco?.editor.getModels()[0]?.getValue() ?? "",
    );
    assert.equal(
      applied,
      template.code,
      `template ${template.id} code was not applied verbatim`,
    );
    const expectedRows = Object.keys(template.schema.properties).length;
    await expect(page.locator(".strategy-schema-row")).toHaveCount(expectedRows);
    await page.getByRole("button", { name: "静态校验", exact: true }).click();
    await expect(page.getByRole("status")).toContainText("静态校验通过", {
      timeout: 15000,
    });
    checked.push(template.id);
  }
  assert.equal(checked.length, library.length);
  step("全部模板逐一应用并通过服务端静态校验", { checked });

  await templatePanel.getByRole("button", { name: "横截面动量" }).click();
  await expect(page.locator(".strategy-schema-row")).toHaveCount(5);

  const topNRow = page
    .locator(".strategy-schema-row")
    .filter({ has: page.locator('input[value="top_n"]') });
  await expect(topNRow).toHaveCount(1);
  const defaultValueInput = topNRow
    .locator("label")
    .filter({ hasText: "默认值" })
    .locator("input");
  await defaultValueInput.fill("3");
  await page.getByRole("button", { name: "JSON", exact: true }).click();
  const jsonText = await page
    .getByLabel("参数 schema JSON")
    .inputValue();
  assert.ok(jsonText.includes('"top_n"'), "top_n missing from JSON view");
  assert.match(jsonText, /"default": 3/);
  step("表单修改同步到 JSON 视图");
  await page.getByRole("button", { name: "表单", exact: true }).click();
  await expect(page.locator(".strategy-schema-row")).toHaveCount(5);

  await page.getByLabel("策略名称", { exact: true }).fill(strategyName);
  await page.getByLabel("用途说明", { exact: true }).fill(
    "模板发起的横截面动量研究，验证参数表单与内联诊断。",
  );

  await page.evaluate(() => {
    const monaco = window.monaco;
    const model = monaco.editor.getModels()[0];
    model.applyEdits([
      {
        range: new monaco.Range(1, 1, 1, 1),
        text: "import os\n",
        forceMoveMarkers: true,
      },
    ]);
  });
  await page.getByRole("button", { name: "静态校验", exact: true }).click();
  const diagnosticsList = page.getByRole("list", { name: "静态校验结果" });
  await expect(diagnosticsList).toBeVisible();
  await expect(diagnosticsList).toContainText("capability.");
  const markers = await page.evaluate(
    () =>
      window.monaco?.editor
        .getModelMarkers({ owner: "qtrade-static" })
        .map((marker) => ({
          line: marker.startLineNumber,
          message: marker.message,
        })) ?? [],
  );
  assert.ok(markers.length > 0, "no inline markers were set");
  assert.ok(
    markers.some((marker) => marker.line === 1),
    `expected a marker on line 1, saw ${JSON.stringify(markers)}`,
  );
  await page.screenshot({
    path: `${directory}/desktop-inline-diagnostic.png`,
    fullPage: true,
  });
  step("违规导入触发内联诊断", { markers });

  await page.evaluate(() => {
    const monaco = window.monaco;
    const model = monaco.editor.getModels()[0];
    model.applyEdits([
      { range: new monaco.Range(1, 1, 2, 1), text: "", forceMoveMarkers: true },
    ]);
  });
  await expect
    .poll(async () =>
      page.evaluate(
        () => window.monaco?.editor.getModels()[0]?.getValue() ?? "",
      ),
    )
    .not.toContain("import os");
  await page.getByRole("button", { name: "静态校验", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("静态校验通过");
  step("撤销违规代码后静态校验通过");

  await page.getByRole("button", { name: "创建策略", exact: true }).click();
  await expect(page).toHaveURL(/strategy_id=/);
  await expect(
    page.getByRole("heading", { name: strategyName, exact: true }),
  ).toBeVisible();
  await expect(page.locator(".strategy-version-list li")).toHaveCount(1);
  const strategyId = new URL(page.url()).searchParams.get("strategy_id");
  step("保存模板策略", { strategyId });

  await page.getByRole("button", { name: "静态校验", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("静态校验通过");
  await page.getByRole("button", { name: "校验参数", exact: true }).click();
  const backtestLink = page.getByRole("link", { name: "配置回测" });
  await expect(backtestLink).toBeVisible({ timeout: 15000 });
  await backtestLink.click();
  await expect(page).toHaveURL(`${origin}/workspace/backtests`);
  step("参数校验后可进入回测配置");

  await page.goto(`${origin}/workspace/strategies?strategy_id=${strategyId}`);
  await expect(page.getByRole("heading", { name: strategyName })).toBeVisible();
  await expect(page.locator(".strategy-version-list li")).toHaveCount(1);
  await page.getByRole("button", { name: "JSON", exact: true }).click();
  const persistedSchema = await page
    .getByLabel("参数 schema JSON")
    .inputValue();
  assert.match(persistedSchema, /"default": 3/, "schema default not persisted");
  await page.screenshot({
    path: `${directory}/desktop-strategy-persisted.png`,
    fullPage: true,
  });
  step("刷新后版本与参数 schema 仍在", { strategyId });

  const overflow = async (label) => {
    const metrics = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
    }));
    report.viewports[label] = metrics;
    assert.ok(
      metrics.scrollWidth <= metrics.innerWidth + 1,
      `${label} viewport overflows horizontally: ${JSON.stringify(metrics)}`,
    );
  };
  await overflow("desktop");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${origin}/workspace/strategies`);
  await expect(page.getByRole("region", { name: "策略模板" })).toBeVisible();
  await overflow("mobile-new");
  await page.screenshot({
    path: `${directory}/mobile-templates.png`,
    fullPage: true,
  });
  await page
    .getByRole("region", { name: "策略模板" })
    .getByRole("button", { name: /双均线交叉/ })
    .click();
  await expect(page.locator(".strategy-schema-row")).toHaveCount(4);
  await overflow("mobile-form");
  await page.screenshot({
    path: `${directory}/mobile-schema-form.png`,
    fullPage: true,
  });
  step("手机宽度无横向溢出");

  const unexpected = report.consoleErrors.filter(
    (message) => !message.includes("favicon"),
  );
  assert.deepEqual(unexpected, [], `console errors: ${unexpected.join(" | ")}`);
  step("控制台无错误");

  writeFileSync(
    `${directory}/report.json`,
    JSON.stringify(report, null, 2) + "\n",
  );
  console.log("strategy authoring verification passed");
} finally {
  await browser.close();
}