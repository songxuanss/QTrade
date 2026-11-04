import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("workspace health page summarizes authorized operational sources", async ({
  page,
}) => {
  await page.goto("/workspace/strategies");
  await expect(page.getByText("身份已验证", { exact: true })).toBeVisible();
  await page.goto("/workspace/health");
  await expect(
    page.getByRole("heading", { name: "系统与数据健康" }),
  ).toBeVisible();
  await expect(page.getByText("行情流", { exact: true })).toBeVisible();
  await expect(page.getByText("测试交易通道", { exact: true })).toBeVisible();
  await expect(page.getByText("正常", { exact: true })).toHaveCount(2);
  await expect(page.locator(".health-metric strong")).toHaveText([
    "0",
    "0",
    "0",
  ]);
  await expect(
    page.getByRole("link", { name: "查看采集任务" }),
  ).toHaveAttribute("href", "/workspace/data#collection");
  await expect(
    page.getByRole("link", { name: "查看质量检查" }),
  ).toHaveAttribute("href", "/workspace/data#quality");
  await expect(
    page.getByRole("link", { name: "打开市场终端 ↗" }),
  ).toHaveAttribute("href", "/workspace/market");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.body.scrollWidth <= innerWidth),
  ).toBe(true);
});

test("alert rules validate, toggle through immutable versions, and reject viewer writes", async ({
  page,
}) => {
  await page.goto("/workspace/strategies");
  await expect(page.getByText("身份已验证", { exact: true })).toBeVisible();
  await page.goto("/workspace/health");
  const session = await (await page.request.get("/auth/session")).json();
  const cookie = (await page.context().cookies("http://127.0.0.1:3110")).find(
    (item) => item.name.endsWith("qtrade-session"),
  );
  expect(cookie).toBeTruthy();
  const headers = {
    Cookie: `qtrade-session=${cookie!.value}`,
    Origin: "http://127.0.0.1:3110",
    "X-CSRF-Token": session.csrf_token,
  };
  const base = `http://127.0.0.1:3111/api/v1/workspaces/${session.workspace_id}/alert-rules`;
  await page.request.post("http://127.0.0.1:3111/fixture/alert-sources", {
    params: { workspace_id: session.workspace_id },
  });
  const events = await (
    await page.request.get(`${base}/events`, { headers })
  ).json();
  expect(
    events.map((event: { source: string }) => event.source).sort(),
  ).toEqual(["data", "market", "risk", "system"]);
  expect(
    events.every(
      (event: { workspace_id: string; severity: string; status: string }) =>
        event.workspace_id === session.workspace_id &&
        event.severity === "warning" &&
        event.status === "open",
    ),
  ).toBe(true);
  const eventId = events[0].event_id as string;
  expect(
    (await page.request.get(`${base}/events/${eventId}`, { headers })).status(),
  ).toBe(200);
  expect(
    (
      await page.request.get(`${base}/events/${eventId}/history`, { headers })
    ).status(),
  ).toBe(200);
  await page.goto(`/workspace/alerts?event_id=${eventId}`);
  await expect(page.getByRole("heading", { name: "告警中心" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Source health check" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "认领" })).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  const lifecycleViewer = await page.request.get(
    "http://127.0.0.1:3111/fixture/viewer-session",
    { params: { workspace_id: session.workspace_id } },
  );
  const lifecycleViewerContext = await lifecycleViewer.json();
  const deniedHandle = await page.request.post(
    `${base}/events/${eventId}/actions/close`,
    {
      headers: {
        Cookie: `qtrade-session=${lifecycleViewerContext.cookie}`,
        Origin: "http://127.0.0.1:3110",
        "X-CSRF-Token": lifecycleViewerContext.csrf_token,
      },
      data: {},
    },
  );
  expect(deniedHandle.status()).toBe(403);
  for (const action of ["claim", "acknowledge"] as const) {
    expect(
      (
        await page.request.post(`${base}/events/${eventId}/actions/${action}`, {
          headers,
          data: {},
        })
      ).status(),
    ).toBe(200);
  }
  expect(
    (
      await page.request.post(`${base}/events/${eventId}/actions/silence`, {
        headers,
        data: { silenced_until: new Date(Date.now() + 3600_000).toISOString() },
      })
    ).status(),
  ).toBe(200);
  expect(
    (
      await page.request.post(`${base}/events/${eventId}/actions/note`, {
        headers,
        data: { note: "已联系供应商排查" },
      })
    ).status(),
  ).toBe(200);
  expect(
    (
      await page.request.post(`${base}/events/${eventId}/actions/close`, {
        headers,
        data: {},
      })
    ).status(),
  ).toBe(200);
  const finalEvent = await (
    await page.request.get(`${base}/events/${eventId}`, { headers })
  ).json();
  expect(finalEvent.status).toBe("closed");
  expect(finalEvent.assignee_id).toBe(session.user_id);
  const history = await (
    await page.request.get(`${base}/events/${eventId}/history`, { headers })
  ).json();
  expect(history.map((item: { action: string }) => item.action)).toEqual([
    "claim",
    "acknowledge",
    "silence",
    "note",
    "close",
  ]);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  const created = await page.request.post(base, {
    headers,
    data: {
      name: "价格阈值",
      source: "market",
      condition: "price > 100",
      severity: "warning",
      cooldown_seconds: 60,
    },
  });
  expect(created.status()).toBe(201);
  const rule = await created.json();
  expect(rule.version).toBe(1);
  expect(
    (
      await page.request.patch(`${base}/${rule.rule_id}/enabled`, {
        headers,
        data: { enabled: false },
      })
    ).status(),
  ).toBe(200);
  const versions = await (
    await page.request.get(`${base}/${rule.rule_id}/versions`, { headers })
  ).json();
  expect(
    versions.map((item: { version: number; enabled: boolean }) => [
      item.version,
      item.enabled,
    ]),
  ).toEqual([
    [1, true],
    [2, false],
  ]);
  const invalid = await page.request.post(base, {
    headers,
    data: {
      name: "bad",
      source: "unknown",
      condition: "x",
      severity: "warning",
      cooldown_seconds: 60,
    },
  });
  expect(invalid.status()).toBe(422);
  const viewer = await page.request.get(
    "http://127.0.0.1:3111/fixture/viewer-session",
    { params: { workspace_id: session.workspace_id } },
  );
  const viewerContext = await viewer.json();
  const denied = await page.request.post(base, {
    headers: {
      Cookie: `qtrade-session=${viewerContext.cookie}`,
      Origin: "http://127.0.0.1:3110",
      "X-CSRF-Token": viewerContext.csrf_token,
    },
    data: {
      name: "Viewer rule",
      source: "market",
      condition: "price > 1",
      severity: "info",
      cooldown_seconds: 1,
    },
  });
  expect(denied.status()).toBe(403);
  const audit = await (
    await page.request.get("http://127.0.0.1:3111/fixture/audit")
  ).json();
  expect(audit).toContainEqual({
    action: "alert.rule.create",
    result: "success",
  });
  expect(audit).toContainEqual({
    action: "alert.rule.enabled.change",
    result: "success",
  });
  expect(audit).toContainEqual({
    action: "permission.denied",
    result: "denied",
  });
});

test("workspace settings show supported capabilities and unavailable live trading", async ({
  page,
}) => {
  await page.goto("/workspace/settings");
  await expect(page.getByRole("heading", { name: "连接能力" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "测试交易通道" }),
  ).toBeVisible();
  await expect(page.getByText("market · limit", { exact: true })).toBeVisible();
  await expect(
    page.getByText("实盘交易：不可用", { exact: true }),
  ).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await expect(page).toHaveScreenshot("capabilities-desktop.png", {
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  const overflow = await page.evaluate(() => ({
    page: document.documentElement.scrollWidth,
    inner: innerWidth,
    elements: [...document.querySelectorAll("body *")]
      .map((item) => ({
        tag: item.tagName,
        class: typeof item.className === "string" ? item.className : "",
        width: Math.round(item.getBoundingClientRect().width),
        right: Math.round(item.getBoundingClientRect().right),
      }))
      .filter((item) => item.right > innerWidth + 1)
      .slice(-8),
  }));
  expect(overflow.page <= overflow.inner, JSON.stringify(overflow)).toBe(true);
  await expect(page).toHaveScreenshot("capabilities-mobile.png", {
    animations: "disabled",
  });
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("strategy code, parameter schemas and dependencies are immutable by version", async ({
  page,
}) => {
  await page.goto("/workspace/strategies");
  await expect(page.getByText("身份已验证", { exact: true })).toBeVisible();
  const session = await (await page.request.get("/auth/session")).json();
  const cookie = (await page.context().cookies("http://127.0.0.1:3110")).find(
    (item) => item.name.endsWith("qtrade-session"),
  );
  const headers = {
    Cookie: `qtrade-session=${cookie!.value}`,
    Origin: "http://127.0.0.1:3110",
    "X-CSRF-Token": session.csrf_token,
  };
  const base = `http://127.0.0.1:3111/api/v1/workspaces/${session.workspace_id}/strategies`;
  const schema = {
    type: "object",
    properties: { lookback: { type: "integer", minimum: 1, default: 20 } },
    required: ["lookback"],
  };
  const created = await page.request.post(base, {
    headers,
    data: {
      name: "Moving Average",
      description: "Version test",
      code: "def signal(close):\n    return close[-1]\n",
      parameter_schema: schema,
      dependencies: { numpy: "==2.1.0" },
    },
  });
  expect(created.status()).toBe(201);
  const body = await created.json();
  expect(body.version.version).toBe(1);
  expect(body.version.code_sha256).toMatch(/^[a-f0-9]{64}$/);
  const revised = await page.request.post(
    `${base}/${body.strategy.strategy_id}/versions`,
    {
      headers,
      data: {
        code: "def signal(close):\n    return close[-2]\n",
        parameter_schema: {
          ...schema,
          properties: {
            lookback: {
              type: "integer",
              minimum: 1,
              maximum: 200,
              default: 20,
            },
          },
        },
        dependencies: { numpy: "==2.2.0" },
      },
    },
  );
  expect(revised.status()).toBe(201);
  const versions = await (
    await page.request.get(`${base}/${body.strategy.strategy_id}/versions`, {
      headers,
    })
  ).json();
  expect(versions.map((item: { version: number }) => item.version)).toEqual([
    1, 2,
  ]);
  const diff = await (
    await page.request.get(
      `${base}/${body.strategy.strategy_id}/versions/diff?before=1&after=2`,
      { headers },
    )
  ).json();
  expect(diff.code_unified_diff).toContain("close[-2]");
  expect(diff.parameter_schema_changed).toBe(true);
  expect(diff.dependencies_changed).toBe(true);
  const source = await (
    await page.request.get(
      `${base}/${body.strategy.strategy_id}/versions/1/source`,
      { headers },
    )
  ).json();
  expect(source.code).toContain("close[-1]");
  const dependencyManifest = await (
    await page.request.get(
      `${base}/${body.strategy.strategy_id}/versions/1/dependencies`,
      { headers },
    )
  ).json();
  expect(dependencyManifest.dependencies).toEqual({ numpy: "==2.1.0" });
  const staticCases = [
    {
      code: "open('/etc/passwd').read()\n",
      dependencies: {},
      diagnostic: "capability.forbidden_call",
    },
    {
      code: "import requests\nrequests.get('https://example.com')\n",
      dependencies: {},
      diagnostic: "capability.network",
    },
    {
      code: "def signal(close):\n    return close[1]\n",
      dependencies: {},
      diagnostic: "lookahead.positive_index",
    },
    {
      code: "import numpy as np\n",
      dependencies: {},
      diagnostic: "dependency.missing",
    },
  ];
  for (const input of staticCases) {
    const response = await page.request.post(`${base}/static-validation`, {
      headers,
      data: input,
    });
    expect(response.status()).toBe(200);
    const result = await response.json();
    expect(result.passed).toBe(false);
    expect(
      result.diagnostics.map((item: { code: string }) => item.code),
    ).toContain(input.diagnostic);
  }
  await page.goto(
    `/workspace/strategies?strategy_id=${body.strategy.strategy_id}`,
  );
  await expect(page.getByRole("heading", { name: "策略研究" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Moving Average" }),
  ).toBeVisible();
  await expect(page.locator(".strategy-monaco-shell")).toBeVisible();
  await page.getByRole("button", { name: "静态校验" }).click();
  await expect(page.getByRole("status")).toContainText("静态校验通过");
  const lookback = page.locator(".strategy-parameter-fields input").first();
  await expect(lookback).toHaveValue("20");
  await lookback.fill("201");
  await page.getByRole("button", { name: "校验参数" }).click();
  await expect(
    page.getByText("参数类型或取值范围不符合此版本 schema"),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "配置回测" })).toBeDisabled();
  await lookback.fill("50");
  await page.getByRole("button", { name: "校验参数" }).click();
  await expect(page.getByRole("status")).toContainText(
    "参数符合此 Strategy Version",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.body.scrollWidth <= innerWidth),
  ).toBe(true);
  const viewer = await (
    await page.request.get("http://127.0.0.1:3111/fixture/viewer-session", {
      params: { workspace_id: session.workspace_id },
    })
  ).json();
  const denied = await page.request.post(base, {
    headers: {
      Cookie: `qtrade-session=${viewer.cookie}`,
      Origin: "http://127.0.0.1:3110",
      "X-CSRF-Token": viewer.csrf_token,
    },
    data: {
      name: "Denied",
      code: "pass",
      parameter_schema: { type: "object" },
    },
  });
  expect(denied.status()).toBe(403);
  const deniedValidation = await page.request.post(
    `${base}/static-validation`,
    {
      headers: {
        Cookie: `qtrade-session=${viewer.cookie}`,
        Origin: "http://127.0.0.1:3110",
        "X-CSRF-Token": viewer.csrf_token,
      },
      data: { code: "pass\n", dependencies: {} },
    },
  );
  expect(deniedValidation.status()).toBe(403);
});

test("strategy lifecycle requires trusted evidence, scoped approval, and archives history", async ({
  page,
}) => {
  await page.goto("/workspace/strategies");
  await expect(page.getByText("身份已验证", { exact: true })).toBeVisible();
  const session = await (await page.request.get("/auth/session")).json();
  const cookie = (await page.context().cookies("http://127.0.0.1:3110")).find(
    (item) => item.name.endsWith("qtrade-session"),
  );
  const headers = {
    Cookie: `qtrade-session=${cookie!.value}`,
    Origin: "http://127.0.0.1:3110",
    "X-CSRF-Token": session.csrf_token,
  };
  const root = `http://127.0.0.1:3111/api/v1/workspaces/${session.workspace_id}/strategies`;
  const createdResponse = await page.request.post(root, {
    headers,
    data: {
      name: "Lifecycle test",
      description: "",
      code: "def signal(close):\n    return close[-1]\n",
      parameter_schema: { type: "object", properties: {} },
      dependencies: {},
    },
  });
  expect(createdResponse.status()).toBe(201);
  const created = await createdResponse.json();
  const strategyId = created.strategy.strategy_id as string;
  const versionId = created.version.strategy_version_id as string;
  const lifecycle = `${root}/${strategyId}/versions/1/lifecycle`;
  expect(
    (await (await page.request.get(lifecycle, { headers })).json()).state,
  ).toBe("draft");
  const transition = async (state: string) =>
    page.request.post(`${lifecycle}/transitions`, {
      headers,
      data: { state },
    });
  expect((await transition("live")).status()).toBe(403);
  expect((await transition("backtested")).status()).toBe(403);
  expect((await transition("validated")).status()).toBe(200);
  const qualify = async (observations: number) =>
    page.request.post("http://127.0.0.1:3111/fixture/strategy-qualification", {
      params: {
        workspace_id: session.workspace_id,
        strategy_id: strategyId,
        version: 1,
        observations,
      },
    });
  await qualify(0);
  expect((await transition("backtested")).status()).toBe(200);
  await qualify(0);
  expect((await transition("paper")).status()).toBe(200);
  const riskSession = await (
    await page.request.get(
      "http://127.0.0.1:3111/fixture/risk-manager-session",
      {
        params: { workspace_id: session.workspace_id },
      },
    )
  ).json();
  const riskHeaders = {
    Cookie: `qtrade-session=${riskSession.cookie}`,
    Origin: "http://127.0.0.1:3110",
    "X-CSRF-Token": riskSession.csrf_token,
  };
  const approve = () =>
    page.request.post(`${lifecycle}/approve`, {
      headers: riskHeaders,
      data: { reason: "Reviewed" },
    });
  expect((await approve()).status()).toBe(403);
  await qualify(20);
  const approved = await approve();
  expect(approved.status()).toBe(200);
  const approval = (await approved.json()).approval;
  expect(approval.strategy_version_id).toBe(versionId);
  expect(approval.paper_observations).toBe(20);
  expect(
    (await page.request.post(`${lifecycle}/launch`, { headers })).status(),
  ).toBe(403);
  const revised = await page.request.post(`${root}/${strategyId}/versions`, {
    headers,
    data: {
      code: "def signal(close):\n    return close[-2]\n",
      parameter_schema: { type: "object", properties: {} },
      dependencies: {},
    },
  });
  expect(revised.status()).toBe(201);
  expect(
    (
      await (
        await page.request.get(`${root}/${strategyId}/versions/2/lifecycle`, {
          headers,
        })
      ).json()
    ).state,
  ).toBe("draft");
  expect(
    (
      await (
        await page.request.get(`${root}/${strategyId}/versions/2/lifecycle`, {
          headers,
        })
      ).json()
    ).approval,
  ).toBeNull();
  expect(
    (
      await page.request.post(`${root}/${strategyId}/archive`, { headers })
    ).status(),
  ).toBe(200);
  expect(
    (await (await page.request.get(lifecycle, { headers })).json()).state,
  ).toBe("archived");
  expect(
    (await (await page.request.get(lifecycle, { headers })).json()).history.map(
      (item: { to_state: string }) => item.to_state,
    ),
  ).toEqual(["validated", "backtested", "paper", "approved", "archived"]);
  await page.goto(`/workspace/strategies?strategy_id=${strategyId}`);
  await expect(
    page.getByRole("region", { name: "版本生命周期" }),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "版本生命周期" }).getByText("已归档"),
  ).toBeVisible();
  expect(
    (
      await page.request.post(`${root}/${strategyId}/versions`, {
        headers,
        data: {
          code: "def signal(close):\n    return close[-1]\n",
          parameter_schema: { type: "object", properties: {} },
          dependencies: {},
        },
      })
    ).status(),
  ).toBe(403);
  await page.request.post("http://127.0.0.1:3111/fixture/remove-strategy", {
    params: { workspace_id: session.workspace_id, strategy_id: strategyId },
  });
});

test("backtest freezes manifest inputs and persists cancel, progress, and completion", async ({
  page,
}) => {
  await page.goto("/workspace/strategies");
  await expect(page.getByText("身份已验证", { exact: true })).toBeVisible();
  const session = await (await page.request.get("/auth/session")).json();
  const cookie = (await page.context().cookies("http://127.0.0.1:3110")).find(
    (item) => item.name.endsWith("qtrade-session"),
  );
  const headers = {
    Cookie: `qtrade-session=${cookie!.value}`,
    Origin: "http://127.0.0.1:3110",
    "X-CSRF-Token": session.csrf_token,
  };
  const workspaceId = session.workspace_id as string;
  const root = `http://127.0.0.1:3111/api/v1/workspaces/${workspaceId}`;
  const datasets = await (
    await page.request.get("http://127.0.0.1:3111/fixture/datasets", {
      params: { workspace_id: workspaceId },
    })
  ).json();
  expect(datasets.length).toBeGreaterThan(0);
  const created = await page.request.post(`${root}/strategies`, {
    headers,
    data: {
      name: "Backtest queue test",
      code: "def signal(close):\n    return close[-1]\n",
      parameter_schema: { type: "object", properties: {} },
      dependencies: {},
    },
  });
  expect(created.status()).toBe(201);
  const strategy = await created.json();
  const strategyId = strategy.strategy.strategy_id as string;
  expect(
    (
      await page.request.post(
        `${root}/strategies/${strategyId}/versions/1/lifecycle/transitions`,
        { headers, data: { state: "validated" } },
      )
    ).status(),
  ).toBe(200);
  const submit = async (randomSeed: number) =>
    page.request.post(`${root}/backtests`, {
      headers,
      data: {
        strategy_id: strategyId,
        strategy_version: 1,
        dataset_id: datasets[0].dataset_id,
        benchmark_id: "MOCK:ABC",
        start: datasets[0].start,
        end: datasets[0].end,
        initial_cash: "10000",
        currency: "USD",
        cost_model_version: "cost.v1",
        parameters: {},
        execution_model: {
          lot_size: 100,
          fee_bps: "3",
          slippage_bps: "5",
          max_volume_fraction: "0.1",
          latency_bars: 1,
          session_open: "09:30:00",
          session_close: "15:00:00",
          session_breaks: [["11:30:00", "13:00:00"]],
          timezone_name: "Asia/Shanghai",
          daily_price_limit_fraction: "0.1",
        },
        random_seed: randomSeed,
      },
    });
  const first = await submit(17);
  expect(first.status()).toBe(202);
  const queued = await first.json();
  expect(queued.status).toBe("queued");
  expect(queued.manifest_id).toMatch(/^[a-f0-9-]{36}$/i);
  const historyUrl = `${root}/backtests/${queued.run_id}/history`;
  expect(
    (await (await page.request.get(historyUrl, { headers })).json()).map(
      (event: { status: string }) => event.status,
    ),
  ).toEqual(["queued"]);
  await page.request.post(
    `http://127.0.0.1:3111/fixture/backtests/${queued.run_id}/advance`,
    { params: { workspace_id: workspaceId, progress: 45 } },
  );
  const progress = await (
    await page.request.get(`${root}/backtests/${queued.run_id}`, { headers })
  ).json();
  expect(progress.status).toBe("running");
  expect(progress.progress).toBe(45);
  const cancelled = await page.request.post(
    `${root}/backtests/${queued.run_id}/cancel`,
    { headers },
  );
  expect((await cancelled.json()).status).toBe("cancelled");
  expect(
    (
      await page.request.post(`${root}/backtests/${queued.run_id}/cancel`, {
        headers,
      })
    ).status(),
  ).toBe(409);
  const second = await submit(18);
  expect(second.status()).toBe(202);
  const completedRun = await second.json();
  await page.request.post(
    `http://127.0.0.1:3111/fixture/backtests/${completedRun.run_id}/complete`,
    { params: { workspace_id: workspaceId } },
  );
  const completed = await (
    await page.request.get(`${root}/backtests/${completedRun.run_id}`, {
      headers,
    })
  ).json();
  expect(completed.status).toBe("completed");
  expect(completed.progress).toBe(100);
  expect(completed.result_object_key).toContain(completed.run_id);
  const reportResponse = await page.request.get(
    `${root}/backtests/${completedRun.run_id}/report`,
    { headers },
  );
  expect(reportResponse.status()).toBe(200);
  const report = await reportResponse.json();
  expect(report.metrics.sharpe_ratio).toBe("1.2");
  expect(report.fills).toHaveLength(1);
  const manifest = await (
    await page.request.get(
      `${root}/backtests/${completedRun.run_id}/manifest`,
      {
        headers,
      },
    )
  ).json();
  const frozen = JSON.parse(manifest.execution_config_json);
  expect(frozen.execution_model.lot_size).toBe(100);
  expect(frozen.execution_model.session_breaks).toEqual([
    ["11:30:00", "13:00:00"],
  ]);
  expect(manifest.execution_config_sha256).toMatch(/^[a-f0-9]{64}$/);
  const third = await submit(19);
  const comparableRun = await third.json();
  await page.request.post(
    `http://127.0.0.1:3111/fixture/backtests/${comparableRun.run_id}/complete`,
    { params: { workspace_id: workspaceId } },
  );
  const comparison = await page.request.get(
    `${root}/backtests/compare?run_id=${completedRun.run_id}&run_id=${comparableRun.run_id}`,
    { headers },
  );
  expect(comparison.status()).toBe(200);
  const comparisonBody = await comparison.json();
  expect(comparisonBody.comparable).toBe(true);
  expect(comparisonBody.runs).toHaveLength(2);
  expect(comparisonBody.input_differences).toEqual([]);
  const walkForwardPlan = await page.request.get(
    `${root}/backtests/walk-forward/plan`,
    {
      headers,
      params: {
        dataset_id: datasets[0].dataset_id,
        instrument_id: "MOCK:ABC",
        start: new Date(
          Date.parse(datasets[0].end) - 60 * 60_000,
        ).toISOString(),
        end: datasets[0].end,
        train_window_seconds: "600",
        validation_window_seconds: "600",
        step_seconds: "3600",
      },
    },
  );
  expect(walkForwardPlan.status()).toBe(200);
  const walkForward = await walkForwardPlan.json();
  expect(walkForward.windows).toHaveLength(1);
  expect(walkForward.windows[0].train_samples).toBe(10);
  expect(walkForward.windows[0].validation_samples).toBe(10);
  expect(walkForward.windows[0].train_end).toBe(
    walkForward.windows[0].validation_start,
  );
  await page.goto("/workspace/backtests");
  await expect(page.getByRole("heading", { name: "回测实验" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "配置回测" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Walk-forward 样本外运行" }),
  ).toBeVisible();
  const walkForm = page.locator(".walk-forward-form");
  const planStart = await page.evaluate((value) => {
    const date = new Date(Date.parse(value) - 60 * 60_000);
    const pad = (part: number) => String(part).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}.${String(date.getMilliseconds()).padStart(3, "0")}`.replace(
      /(\.\d*?[1-9])0+$|\.0+$/,
      "$1",
    );
  }, datasets[0].end);
  await walkForm
    .locator('input[type="datetime-local"]')
    .first()
    .fill(planStart);
  await walkForm.locator('input[type="number"]').nth(0).fill("0.0069444");
  await walkForm.locator('input[type="number"]').nth(1).fill("0.0069444");
  await walkForm.locator('input[type="number"]').nth(2).fill("0.0416667");
  await walkForm.getByRole("button", { name: "预览切分" }).click();
  await expect(page.locator(".walk-forward-results tbody tr")).toHaveCount(1);
  await expect(page.locator(".walk-forward-results tbody tr")).toContainText(
    "10",
  );
  await expect(page.getByText("已取消", { exact: true })).toBeVisible();
  await expect(page.getByText("已完成", { exact: true })).toHaveCount(2);
  await page.getByRole("link", { name: "对比最近两次完成回测" }).click();
  await expect(page.getByRole("heading", { name: "回测对比" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "核心指标" })).toBeVisible();
  await expect(page.getByRole("img", { name: /归一化权益曲线/ })).toHaveCount(
    2,
  );
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.body.scrollWidth <= innerWidth),
  ).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/workspace/backtests");
  await expect(
    page.getByRole("heading", { name: "回测实验", exact: true }),
  ).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page
    .getByRole("link", { name: /查看报告/ })
    .first()
    .click();
  await expect(page.getByRole("heading", { name: "回测报告" })).toBeVisible();
  await expect(page.getByRole("img", { name: /组合与基准对比/ })).toBeVisible();
  await expect(page.getByRole("img", { name: /回撤曲线/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: "成交明细" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "订单结果" })).toBeVisible();
  await expect(page.getByText("部分成交，剩余未成交")).toBeVisible();
  await expect(page.getByRole("heading", { name: "期末持仓" })).toBeVisible();
  await expect(
    page.locator(".backtest-position-table").getByRole("cell", {
      name: "MOCK:ABC",
    }),
  ).toBeVisible();
  await expect(page.getByText("Asia/Shanghai")).toBeVisible();
  await expect(page.getByText("基准 MOCK:ABC")).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.body.scrollWidth <= innerWidth),
  ).toBe(true);
  await page.goto("/workspace/backtests");
  await page.getByRole("button", { name: "创建 Backtest Run" }).click();
  await expect(
    page.getByRole("status").getByText("回测已排队，输入快照已冻结。"),
  ).toBeVisible();
  await expect(page.getByText("排队中", { exact: true })).toBeVisible();
  await page.request.post("http://127.0.0.1:3111/fixture/backtests/reset");
  await page.request.post("http://127.0.0.1:3111/fixture/remove-strategy", {
    params: { workspace_id: workspaceId, strategy_id: strategyId },
  });
});

test("command palette keeps keyboard focus and active options accessible", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 320 });
  await page.goto("/workspace/market");
  await expect(page.getByRole("heading", { name: "市场终端" })).toBeVisible();
  const trigger = page.getByRole("button", { name: "打开命令面板" });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "命令面板" });
  const commandSearch = dialog.getByRole("combobox", {
    name: "搜索标的或页面",
  });
  const listbox = dialog.getByRole("listbox", { name: "搜索结果" });
  await expect(commandSearch).toBeFocused();
  await expect(commandSearch).toHaveAttribute("aria-expanded", "true");
  const listboxId = await listbox.getAttribute("id");
  expect(listboxId).toBeTruthy();
  await expect(commandSearch).toHaveAttribute("aria-controls", listboxId!);

  await page.keyboard.press("Tab");
  await expect(commandSearch).toBeFocused();
  await commandSearch.fill("workspace");
  const options = dialog.getByRole("option");
  await expect(options).toHaveCount(5);
  await expect(dialog.getByRole("status")).toHaveCount(0);
  const firstOptionId = await options.first().getAttribute("id");
  expect(firstOptionId).toBeTruthy();
  await expect(commandSearch).toHaveAttribute(
    "aria-activedescendant",
    firstOptionId!,
  );
  await expect(options.first()).toHaveAttribute("aria-selected", "true");

  for (let index = 0; index < 4; index += 1) {
    await page.keyboard.press("ArrowDown");
  }
  const activeOptionId = await options.nth(4).getAttribute("id");
  expect(activeOptionId).toBeTruthy();
  await expect(commandSearch).toHaveAttribute(
    "aria-activedescendant",
    activeOptionId!,
  );
  await expect(options.nth(4)).toHaveAttribute("aria-selected", "true");
  await expect(commandSearch).toBeFocused();
  await expect
    .poll(() =>
      options.nth(4).evaluate((option) => {
        const optionRect = option.getBoundingClientRect();
        const listboxRect = option.parentElement!.getBoundingClientRect();
        return (
          optionRect.top >= listboxRect.top - 1 &&
          optionRect.bottom <= listboxRect.bottom + 1
        );
      }),
    )
    .toBe(true);

  await page.keyboard.press("Shift+Tab");
  await expect(options.nth(4)).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(commandSearch).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();

  await page.keyboard.press("Control+k");
  await expect(commandSearch).toBeFocused();
  await commandSearch.fill("Order");
  await expect(dialog.getByRole("option", { name: /交易执行/ })).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/workspace\/trading$/);
});

test("protected workspace logs in through PKCE callback and logs out without exposing tokens", async ({
  page,
}) => {
  // Full PKCE round-trip + Monaco load + two screenshot comparisons + axe on
  // the WSL/9p delivery host needs more than the 30s default; assertions are
  // unchanged and still fail fast on CI runners.
  test.setTimeout(90_000);
  await page.request.post("http://127.0.0.1:3111/fixture/strategies/reset");
  await page.goto("/workspace/strategies");
  await expect(page).toHaveURL(/\/workspace\/strategies$/);
  await expect(page.getByText("身份已验证", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("策略研究");
  await expect(page.getByText("工作区管理员", { exact: true })).toBeVisible();
  await expect(page.getByText("工作区已连接", { exact: true })).toBeVisible();
  await expect(page.locator(".monaco-editor")).toBeVisible();
  await expect(page).toHaveScreenshot("workspace-authenticated-desktop.png", {
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page).toHaveScreenshot("workspace-authenticated-mobile.png", {
    animations: "disabled",
  });
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 1280, height: 720 });
  const cookies = await page.context().cookies();
  expect(cookies.map((cookie) => cookie.name)).toEqual(["qtrade-session"]);
  expect(cookies[0].httpOnly).toBe(true);
  expect(await page.evaluate(() => document.cookie)).toBe("");
  expect(
    await page.evaluate(() => [localStorage.length, sessionStorage.length]),
  ).toEqual([0, 0]);
  const context = await (await page.request.get("/auth/session")).json();
  expect(context.authenticated).toBe(true);
  const visible =
    (await page.content()) + JSON.stringify(context) + JSON.stringify(cookies);
  for (const secret of [
    "bff-private-access",
    "bff-private-refresh",
    "browser-fixture-client-secret",
    "browser-fixture-bff-secret",
  ])
    expect(visible).not.toContain(secret);
  await page.goto("/workspace/data");
  await expect(page.getByRole("heading", { name: "数据管理" })).toBeVisible();
  const primary = page.getByRole("tablist", { name: "数据管理分区" });
  await expect(primary.getByRole("tab", { name: "多维下载" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  // 逐笔与兼容 is now a nested entry of the 多维下载 view (research_view=ticks).
  await page.getByRole("tab", { name: "逐笔与兼容", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "逐笔与兼容历史下载" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "下载指定时段" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "创建 Tick 下载任务" }),
  ).toBeVisible();
  await expect(page.locator('input[name="source"]')).toHaveValue(
    "mairui.a-share.tick",
  );
  await expect(
    page.locator('.tick-request-form input[name="instrument_id"]'),
  ).toHaveAttribute("pattern", "(?:CN\\.)?\\d{6}(?:\\.(?:SH|SZ|BJ))?");
  await expect(
    page.getByText("港股历史逐笔源暂未接通。", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByText("A 股历史逐笔来源待配置", { exact: true }),
  ).toBeVisible();
  await page
    .locator(".legacy-history-entry")
    .getByText("旧版日线与分钟下载入口")
    .click();
  await expect(
    page.getByRole("heading", { name: "日线与分钟历史下载" }),
  ).toBeVisible();
  await primary.getByRole("tab", { name: "任务与结果" }).click();
  await page.getByRole("tab", { name: "原始批次" }).click();
  await expect(page.getByRole("heading", { name: "原始批次" })).toBeVisible();
  await page.getByRole("tab", { name: "质量问题" }).click();
  await expect(page.getByRole("heading", { name: "质量问题" })).toBeVisible();
  await expect(
    page.getByText("暂时无法读取数据管理服务", { exact: false }),
  ).toHaveCount(0);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.body.scrollWidth <= innerWidth),
  ).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "组合与风险摘要" }),
  ).toBeVisible();
  await expect(page.getByText("演示组合 · 非真实账户")).toBeVisible();
  await expect(page.getByText("US$26,960.00", { exact: true })).toBeVisible();
  await expect(page.getByRole("row", { name: /MOCK:ABC/ })).toBeVisible();
  await expect(page.getByText("10.13%", { exact: true })).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.goto("/workspace/strategies");
  let socketConnections = 0;
  await page.routeWebSocket(
    "ws://127.0.0.1:3111/api/v1/workspaces/*/market-streams/ws/mock-feed/MOCK%3AABC",
    (socket) => {
      socketConnections += 1;
      if (socketConnections === 1) {
        void socket.close({ code: 1012, reason: "fixture connection failure" });
      } else if (socketConnections === 2) {
        socket.send(JSON.stringify({ status: "healthy", latest_lag_ms: 12 }));
        setTimeout(
          () => void socket.close({ code: 1012, reason: "fixture restart" }),
          40,
        );
      } else {
        socket.send(
          JSON.stringify({ status: "degraded", latest_lag_ms: 2500 }),
        );
      }
    },
  );
  await page.goto("/workspace/market");
  await expect(page.getByRole("heading", { name: "市场终端" })).toBeVisible();
  await expect(page.locator(".market-stream-status")).toContainText("2500 ms");
  expect(socketConnections).toBeGreaterThanOrEqual(2);
  await page.keyboard.press("Control+k");
  const commandSearch = page.getByRole("combobox", {
    name: "搜索标的或页面",
  });
  await expect(commandSearch).toBeFocused();
  await commandSearch.fill("MOCK:ABC");
  await page
    .getByRole("dialog")
    .getByRole("option", { name: /MOCK:ABC/ })
    .click();
  await expect(page).toHaveURL(/\/workspace\/market\?instrument=MOCK%3AABC/);
  await expect(
    page.getByRole("heading", { name: "MOCK:ABC K 线图" }),
  ).toBeVisible();
  await page.keyboard.press("Control+k");
  await page
    .getByRole("combobox", {
      name: "搜索标的或页面",
    })
    .fill("Order");
  await page
    .getByRole("dialog")
    .getByRole("option", { name: /交易执行/ })
    .click();
  await expect(page).toHaveURL(/\/workspace\/trading$/);
  await expect(page.getByRole("heading", { name: "交易执行" })).toBeVisible();
  await page.goto("/workspace/market");
  await expect(page.getByRole("row", { name: /MOCK:ABC/ })).toBeVisible();
  await expect(page.getByText("101", { exact: true })).toBeVisible();
  await page.getByText("自定义列").click();
  await page.getByRole("checkbox", { name: "成交量" }).uncheck();
  await page.reload();
  await expect(page.getByRole("row", { name: /MOCK:ABC/ })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "成交量" })).toHaveCount(
    0,
  );
  await page.getByRole("button", { name: "显示 MOCK:ABC K 线图" }).click();
  await expect(
    page.getByRole("heading", { name: "MOCK:ABC K 线图" }),
  ).toBeVisible();
  await expect(
    page.getByRole("img", { name: "MOCK:ABC 1m K 线与成交量" }),
  ).toBeVisible();
  await expect(page.locator(".indicator-ma")).toHaveAttribute("d", /M.+L/);
  await expect(page.getByText("MACD", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "EMA 20" }).click();
  await expect(page.locator(".indicator-ema")).toHaveAttribute("d", /M.+L/);
  await page.getByRole("button", { name: "BOLL 20,2" }).click();
  await expect(page.locator(".indicator-boll")).toHaveCount(2);
  await expect(page.getByText(/发现 1 处较长时间间隔/)).toBeVisible();
  await expect(
    page.getByRole("link", { name: "查看数据质量" }),
  ).toHaveAttribute("href", "/workspace/data#quality");
  await page.getByRole("button", { name: "1h", exact: true }).click();
  await expect(
    page.getByText("行情服务暂时不可用，请重试或稍后刷新。", {
      exact: true,
    }),
  ).toBeVisible();
  await page.getByRole("button", { name: "1 周" }).click();
  await expect(page.getByRole("button", { name: "1 周" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await page.getByRole("button", { name: "5m", exact: true }).click();
  await expect(page.getByText("该周期暂无可用行情数据")).toBeVisible();
  await expect(page.getByRole("link", { name: "查看数据批次" })).toBeVisible();
  await page.getByRole("button", { name: "1m", exact: true }).click();
  await expect(
    page.getByRole("img", { name: "MOCK:ABC 1m K 线与成交量" }),
  ).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.body.scrollWidth <= innerWidth),
  ).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/workspace/strategies");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await expect(page.getByText("访客", { exact: true })).toBeVisible();
  expect((await page.request.get("/auth/session")).status()).toBe(401);
});

test("bad signature cannot map an identity or create a local session", async ({
  page,
  request,
}) => {
  const before = await (
    await request.get("http://127.0.0.1:3111/stats")
  ).json();
  const start = await page.request.get("/auth/login", { maxRedirects: 0 });
  const url = new URL(start.headers().location);
  url.searchParams.set("mode", "signature");
  await page.goto(url.href);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "登录未完成",
  );
  const after = await (await request.get("http://127.0.0.1:3111/stats")).json();
  expect(after.exchanges).toBe(before.exchanges + 1);
  expect(after.sessions).toBe(before.sessions);
  expect(after.mapped).toBe(before.mapped);
  expect(after.failed_logins).toBe(before.failed_logins + 1);
  expect((await page.context().cookies()).map((cookie) => cookie.name)).toEqual(
    [],
  );
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("missing state fails before exchange and forged logout cannot revoke", async ({
  page,
  request,
}) => {
  const before = await (
    await request.get("http://127.0.0.1:3111/stats")
  ).json();
  await page.goto("/auth/callback?code=forged");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "登录未完成",
  );
  const after = await (await request.get("http://127.0.0.1:3111/stats")).json();
  expect(after.exchanges).toBe(before.exchanges);
  expect(after.failed_logins).toBe(before.failed_logins + 1);
  await page.goto("/workspace/data");
  await expect(page.getByText("身份已验证", { exact: true })).toBeVisible();
  const context = await (await page.request.get("/auth/session")).json();
  const forged = await page.request.post("/auth/logout", {
    headers: {
      Origin: "https://evil.test",
      "X-CSRF-Token": context.csrf_token,
    },
  });
  expect(forged.status()).toBe(403);
  expect((await page.request.get("/auth/session")).status()).toBe(200);
});

test("signed global logout revokes all tabs and rejects replay after a new login", async ({
  page,
  request,
}) => {
  await page.goto("/workspace/strategies");
  await expect(page.getByText("身份已验证", { exact: true })).toBeVisible();
  const jti = "browser-logout-" + Date.now();
  const token = await (
    await request.get("http://127.0.0.1:3111/fixture/logout-token", {
      params: { jti },
    })
  ).json();
  const bad = await (
    await request.get("http://127.0.0.1:3111/fixture/logout-token", {
      params: { jti: jti + "-bad", mode: "signature" },
    })
  ).json();
  expect(
    (await request.post("/auth/backchannel-logout", { data: bad })).status(),
  ).toBe(401);
  expect((await page.request.get("/auth/session")).status()).toBe(200);
  const other = await page.context().newPage();
  expect(
    (await request.post("/auth/backchannel-logout", { form: token })).status(),
  ).toBe(200);
  expect((await page.request.get("/auth/session")).status()).toBe(401);
  expect((await other.request.get("/auth/session")).status()).toBe(401);
  await other.close();
  await page.goto("/auth/login?returnTo=/workspace/strategies");
  await expect(page.getByText("身份已验证", { exact: true })).toBeVisible();
  expect(
    (await request.post("/auth/backchannel-logout", { data: token })).status(),
  ).toBe(401);
  expect((await page.request.get("/auth/session")).status()).toBe(200);
});

test("authenticated mobile drawer navigation reaches workspace sections", async ({
  page,
}) => {
  // Companion to the public-page drawer assertions in tests/e2e/
  // baseline.spec.ts: the e2e dev server has no auth environment, so only
  // this suite can exercise the authenticated half of mobile navigation.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/workspace/health");
  // 身份已验证 lives in the sidebar, which is display:none at this width
  // until the drawer opens — the health heading is the guest-free signal.
  await expect(
    page.getByRole("heading", { name: "系统与数据健康" }),
  ).toBeVisible();
  const menuButton = page.getByRole("button", { name: /主导航/ });
  await menuButton.click();
  await expect(page.getByText("身份已验证", { exact: true })).toBeVisible();
  await expect(page.locator(".nav-item").first()).toBeFocused();
  await expect(page.locator(".app-body")).toHaveAttribute("inert", "");
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "策略研究" })
    .click();
  await expect(page).toHaveURL(/\/workspace\/strategies$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("策略研究");
  expect(
    await page.evaluate(() => document.body.scrollWidth <= innerWidth),
  ).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});
