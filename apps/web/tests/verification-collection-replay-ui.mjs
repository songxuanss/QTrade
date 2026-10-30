/** Real archived supplier inputs, current sessions and deployed Worker.
 * Offline success must not be reported as a new supplier acquisition. */
import assert from "node:assert/strict";
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
const read = (p) => JSON.parse(readFileSync(`../../.tools/${p}`));
const admin = read("sso-local/state.json");
const reader = read("verification-local/approver.json");
const scope = read("verification-local/budget-scope.json").scope_id;
const namespace = process.env.QTRADE_REPLAY_REPORT ?? "collection-replay-ui";
assert(/^[a-z0-9-]+$/.test(namespace));
const barsOnly = process.argv.includes("--bars-only");
const file = `../../.tools/verification-local/review/${namespace}.json`;
const report = existsSync(file)
  ? JSON.parse(readFileSync(file))
  : {
      scope_id: scope,
      targets: read("verification-local/review/normalization-input-ui.json")
        .runs.flatMap((r) =>
          r.parts.map((p) => ({
            kind: r.kind,
            parent_job_id: r.job_id,
            parent_part_id: p.part_id,
            original_publication_id: p.publication_id,
            records: p.records,
            field: p.field,
          })),
        )
        .filter((t) => !barsOnly || t.kind === "bars"),
      checks: [],
    };
const save = () =>
  writeFileSync(file, JSON.stringify(report, null, 2), { mode: 0o600 });
const origin = "http://127.0.0.1:13000";
const base = `http://127.0.0.1:3122/api/v1/workspaces/${scope}/data/collector`;
const readOnly = process.argv.includes("--read-only");
const inputsOnly = process.argv.includes("--inputs-only");
assert(
  !readOnly ||
    report.targets.every((t) => t.replay_id && t.replay_publication_id),
);
const browser = await chromium.launch();
let phase = "login";
async function json(page, path) {
  const response = await page.request.get(`${base}/${path}`);
  assert.equal(response.status(), 200);
  return response.json();
}
async function login(page, role) {
  await page.goto(origin + "/workspace/data", { timeout: 90000 });
  await page
    .getByRole("textbox", { name: "邮箱", exact: true })
    .fill(role === "admin" ? "qtrade-integration@example.test" : reader.email);
  await page
    .getByLabel("密码", { exact: true })
    .fill(role === "admin" ? admin.password : reader.password);
  await page.getByRole("button", { name: "登 录", exact: true }).click();
  await page.waitForURL(
    (u) => ["/workspace/data", "/auth/consent"].includes(u.pathname),
    { timeout: 90000 },
  );
  if (new URL(page.url()).pathname === "/auth/consent")
    await page.getByRole("button", { name: "同意授权", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "数据管理", exact: true }),
  ).toBeVisible({ timeout: 90000 });
  if (!(await page.getByLabel("切换工作区", { exact: true }).isVisible()))
    await page.getByRole("button", { name: "打开主导航", exact: true }).click();
  await page.getByLabel("切换工作区", { exact: true }).selectOption(scope);
  await page.getByRole("button", { name: "切换", exact: true }).click();
  await expect(page).toHaveURL(origin + "/", { timeout: 60000 });
}
async function dialogQuality(page, name) {
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    const axe = await new AxeBuilder({ page })
      .include("dialog[open]")
      .analyze();
    assert.equal(
      axe.violations.length,
      0,
      axe.violations.map((v) => v.id).join(","),
    );
    await page.screenshot({
      path: `../../.tools/verification-local/review/collection-replay-${name}-${width}.png`,
    });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
}
try {
  for (const role of ["admin", "reader"]) {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
    });
    const page = await context.newPage();
    page.setDefaultTimeout(30000);
    let browserErrors = 0;
    page.on("pageerror", () => browserErrors++);
    phase = role + " login";
    await login(page, role);
    const session = await (
      await page.request.get("http://127.0.0.1:3122/auth/session")
    ).json();
    const csrf = { "X-CSRF-Token": session.csrf_token };
    const anonymous = await browser.newContext();
    const sample = report.targets[0];
    assert(
      [401, 403].includes(
        (
          await anonymous.request.get(
            `${base}/jobs/${sample.parent_job_id}/parts/${sample.parent_part_id}/normalization-inputs`,
          )
        ).status(),
      ),
    );
    await anonymous.close();
    for (const [index, target] of report.targets.entries()) {
      phase = role + " " + index + " input metadata";
      const beforeUsage = await json(page, "usage");
      const beforeParent = await json(page, `jobs/${target.parent_job_id}`);
      const catalog = await json(
        page,
        `jobs/${target.parent_job_id}/parts/${target.parent_part_id}/normalization-inputs`,
      );
      assert.equal(catalog.scope_id, scope);
      assert(
        catalog.inputs.length > 0 &&
          !JSON.stringify(catalog).includes('"rows"'),
      );
      const choice = catalog.inputs[0];
      const draft = {
        request_id: crypto.randomUUID(),
        parent_job_id: target.parent_job_id,
        parent_part_id: target.parent_part_id,
        input_id: choice.input_id,
      };
      assert.equal(
        (
          await page.request.post(`${base}/replays/preview`, { data: draft })
        ).status(),
        403,
      );
      if (role === "reader")
        assert.equal(
          (
            await page.request.post(`${base}/replays/preview`, {
              headers: csrf,
              data: draft,
            })
          ).status(),
          403,
        );
      else
        assert.equal(
          (
            await page.request.post(`${base}/replays/preview`, {
              headers: csrf,
              data: { ...draft, actor_id: crypto.randomUUID() },
            })
          ).status(),
          422,
        );
      const outside = await page.request.get(
        `http://127.0.0.1:3122/api/v1/workspaces/${crypto.randomUUID()}/data/collector/jobs/${target.parent_job_id}/parts/${target.parent_part_id}/normalization-inputs`,
      );
      assert([403, 404].includes(outside.status()));
      if (target.replay_id) {
        const previous = await page.request.get(
          `${base}/jobs/${target.replay_id}`,
        );
        if (previous.status() === 404 && role === "admin" && !readOnly) {
          delete target.replay_id;
          save();
        } else assert.equal(previous.status(), 200);
      }
      const create = role === "admin" && !target.replay_id;
      const jobId =
        inputsOnly || create ? target.parent_job_id : target.replay_id;
      assert(jobId);
      await page.goto(
        origin + `/workspace/data?data_view=collect&collector_job=${jobId}`,
        { timeout: 90000 },
      );
      const panel = page.locator("#independent-collector");
      await expect(panel.locator("[data-collector-job]")).toHaveAttribute(
        "data-collector-job",
        jobId,
        { timeout: 90000 },
      );
      const selectedJob = await json(page, `jobs/${jobId}`);
      const partId =
        inputsOnly || create
          ? target.parent_part_id
          : selectedJob.parts[0].part_id;
      await panel
        .locator(`[data-collection-replay="${partId}"]`)
        .getByRole("button", {
          name: role === "admin" ? "离线重处理" : "转换输入与血缘",
          exact: true,
        })
        .focus();
      await page.keyboard.press("Enter");
      const dialog = page.getByRole("dialog", {
        name: "行情与因子离线重处理",
        exact: true,
      });
      await expect(dialog).toBeVisible();
      if (inputsOnly) {
        phase = role + " input selection layout";
        await dialog
          .getByLabel("选择完整转换输入", { exact: true })
          .selectOption(choice.input_id);
        await expect(
          dialog.getByLabel("选择完整转换输入", { exact: true }),
        ).toHaveValue(choice.input_id);
        if (role === "reader")
          await expect(
            dialog.getByRole("button", { name: "预览离线重处理", exact: true }),
          ).toHaveCount(0);
        await dialogQuality(page, `${index}-${role}-input-selection`);
        await dialog
          .getByRole("button", { name: "关闭重处理", exact: true })
          .click();
        report.checks.push({
          role,
          target: index,
          input_selection_desktop_mobile: true,
        });
        save();
        continue;
      }
      if (create) {
        await dialog
          .getByLabel("选择完整转换输入", { exact: true })
          .selectOption(choice.input_id);
        // Controlled browser transport loss is a UI recovery check, not a
        // supplier outage. A failed preview must create no queue task.
        if (index === 0) {
          const interrupted = async (route) => {
            if (route.request().method() === "POST") await route.abort();
            else await route.continue();
          };
          await page.route("**/workspace/data*", interrupted);
          await dialog
            .getByRole("button", { name: "预览离线重处理", exact: true })
            .click();
          await expect(dialog.getByRole("alert")).toContainText("暂不可用");
          await page.unroute("**/workspace/data*", interrupted);
        }
        phase = role + " " + index + " preview";
        const beforeJobs = await json(page, "jobs");
        await dialog
          .getByRole("button", { name: "预览离线重处理", exact: true })
          .click();
        await expect(dialog.locator("[data-replay-request]")).toBeVisible({
          timeout: 90000,
        });
        target.replay_id = await dialog
          .locator("[data-replay-request]")
          .getAttribute("data-replay-request");
        save();
        await expect(dialog).toContainText("供应商调用 0 次");
        assert.deepEqual(await json(page, "jobs"), beforeJobs);
        await dialogQuality(page, `${index}-preview`);
        phase = role + " " + index + " submit worker";
        await dialog
          .getByRole("button", { name: "确认创建离线重处理任务", exact: true })
          .click();
        await expect(dialog.locator("[data-replay-state]")).toBeVisible({
          timeout: 90000,
        });
      } else {
        phase = role + " " + index + " lineage";
        await expect(dialog.locator("[data-replay-request]")).toHaveAttribute(
          "data-replay-request",
          target.replay_id,
          { timeout: 90000 },
        );
        if (role === "reader")
          await expect(
            dialog.getByRole("button", { name: "预览离线重处理", exact: true }),
          ).toHaveCount(0);
      }
      let fresh;
      await expect
        .poll(
          async () => {
            fresh = await json(page, `jobs/${target.replay_id}`);
            return fresh.state;
          },
          { timeout: 90000, intervals: [1000, 2000] },
        )
        .toBe("completed");
      if (target.kind === "bars")
        await expect
          .poll(
            async () => {
              fresh = await json(page, `jobs/${target.replay_id}`);
              return fresh.parts[0].consumer?.state;
            },
            { timeout: 90000 },
          )
          .toBe("visible");
      target.replay_part_id = fresh.parts[0].part_id;
      target.replay_publication_id = fresh.parts[0].publication.publication_id;
      assert.notEqual(
        target.replay_publication_id,
        target.original_publication_id,
      );
      assert.equal(fresh.parts[0].publication.record_count, target.records);
      const intent = await json(page, `replays/${target.replay_id}`);
      assert.equal(intent.command.input_id, choice.input_id);
      target.control_sha256 = intent.control_sha256;
      target.input_sha256 = intent.input.input_sha256;
      target.mapping_version = intent.target_mapping_version;
      let verifiedBars = null;
      if (target.mapping_version === "mairui.history-bars.v3") {
        let offset = 0;
        const records = [];
        do {
          const recordPage = await json(
            page,
            `publications/${target.replay_publication_id}/records?offset=${offset}`,
          );
          records.push(...recordPage.records);
          offset = recordPage.next_offset;
        } while (offset !== null);
        assert.equal(records.length, target.records);
        assert(records.every((r) => r.quality === "valid"));
        verifiedBars = records;
        const checks = await json(
          page,
          `jobs/${target.replay_id}/parts/${target.replay_part_id}/quality`,
        );
        assert(
          checks.some(
            (q) =>
              q.status === "passed" &&
              !q.issues.some((i) => i.code === "source_suspect"),
          ),
        );
        target.numeric_zero_verified = true;
      }
      save();
      if (role === "admin" && !readOnly) {
        const repeated = await page.request.post(`${base}/replays`, {
          headers: csrf,
          data: {
            ...draft,
            request_id: target.replay_id,
            control_sha256: intent.control_sha256,
          },
        });
        assert.equal(repeated.status(), 201);
        assert.equal((await repeated.json()).state, "completed");
      }
      await dialog
        .getByRole("button", { name: "刷新离线处理结果", exact: true })
        .click();
      await expect(
        dialog.locator('[data-replay-state="completed"]'),
      ).toBeVisible({ timeout: 90000 });
      await dialogQuality(page, `${index}-${role}-lineage`);
      assert.deepEqual(
        await json(page, `jobs/${target.parent_job_id}`),
        beforeParent,
      );
      const afterUsage = await json(page, "usage");
      assert.equal(
        afterUsage.scope_budget.credits_used,
        beforeUsage.scope_budget.credits_used,
      );
      assert.equal(afterUsage.request_count, beforeUsage.request_count);
      phase = role + " " + index + " consumer and refresh";
      await dialog
        .getByRole("link", { name: "查看新任务与发布", exact: true })
        .click();
      await expect(panel.locator("[data-collector-job]")).toHaveAttribute(
        "data-collector-job",
        target.replay_id,
        { timeout: 90000 },
      );
      for (const width of [1440, 390]) {
        await page.setViewportSize({
          width,
          height: width === 390 ? 844 : 1000,
        });
        await page.reload({ timeout: 90000 });
        await expect(panel.locator("[data-collector-job]")).toHaveAttribute(
          "data-collector-job",
          target.replay_id,
          { timeout: 90000 },
        );
        await panel
          .getByRole("button", {
            name: target.kind === "bars" ? "查看行情记录" : "查看因子数据",
            exact: true,
          })
          .first()
          .click();
        await expect(
          panel.getByLabel("采集数据预览", { exact: true }).locator("tbody tr"),
        ).toHaveCount(Math.min(100, target.records));
        if (target.numeric_zero_verified) {
          await panel
            .getByRole("button", { name: "查看质量报告", exact: true })
            .first()
            .click();
          await expect(
            panel.getByLabel("采集质量报告", { exact: true }),
          ).toContainText("检查通过");
        }
        assert(
          await page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth,
          ),
        );
        assert.equal(
          (
            await new AxeBuilder({ page })
              .include("#independent-collector")
              .analyze()
          ).violations.length,
          0,
        );
      }
      if (target.kind === "bars") {
        await panel
          .getByRole("link", { name: "终端可见 · 查看图表", exact: true })
          .click();
        await expect(page.locator("#market-chart-heading")).toContainText(
          "农业银行",
        );
        assert.equal(
          new URL(page.url()).searchParams.get("dataset_id"),
          fresh.parts[0].consumer.dataset_id,
        );
        await page.reload({ timeout: 90000 });
        await expect(page.locator(".market-chart svg")).toBeVisible();
        if (verifiedBars) {
          const dataset = fresh.parts[0].consumer.dataset_id;
          const response = await page.request.get(
            `http://127.0.0.1:3122/api/v1/workspaces/${scope}/market-watch/chart?instrument_id=601288.SH&granularity=1d&days=30&dataset_id=${dataset}`,
          );
          assert.equal(response.status(), 200);
          const bars = (await response.json()).bars;
          const until =
            Math.max(...verifiedBars.map((r) => Date.parse(r.timestamp))) +
            86400000;
          const expected = verifiedBars.filter(
            (r) => Date.parse(r.timestamp) >= until - 30 * 86400000,
          );
          assert.deepEqual(
            bars.map((r) => r.timestamp),
            expected.map((r) => r.timestamp),
          );
          assert(
            bars.every(
              (r, i) =>
                r.dataset_id === dataset &&
                Number(r.close) === Number(expected[i].close) &&
                Number(r.volume) === Number(expected[i].volume),
            ),
          );
          target.frozen_chart_records = bars.length;
        }
      }
      report.checks.push({
        role,
        target: index,
        records: target.records,
        desktop_mobile_axe: 0,
        zero_supplier_calls: true,
        refresh: true,
      });
      save();
      await page.setViewportSize({ width: 1440, height: 1000 });
    }
    assert.equal(browserErrors, 0);
    await context.close();
  }
  if (inputsOnly) report.inputs_checked = true;
  else report.passed = true;
  delete report.failed_phase;
  save();
  console.log(JSON.stringify({ passed: true, checks: report.checks }));
} catch (error) {
  report.failed_phase = phase;
  save();
  console.error(JSON.stringify({ passed: false, phase, error: error.name }));
  process.exitCode = 1;
} finally {
  await browser.close();
}
