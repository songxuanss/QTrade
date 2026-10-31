import { workspaceInstrumentNames } from "../data/collector-server";
import { workspaceResearchDatasets } from "../data/research-server";
import "server-only";
import { readSession } from "./session-access";
import { ROLE_NAMES, type PublicWorkspace } from "./public";

export { authConfig } from "./config";
export { beginLogin, completeLogin } from "./oidc";
export {
  sealTransaction,
  openTransaction,
  transactionCookieName,
} from "./transaction";

export async function currentSession(): Promise<boolean> {
  return (await readSession()) !== null;
}

export type WorkspacePortfolioSummary = {
  account_id: string;
  currency: string;
  cash: string;
  equity: string;
  unrealized_pnl: string;
  gross_exposure: string;
  concentration: string;
  drawdown: string;
  open_orders: number;
  positions: {
    instrument_id: string;
    quantity: string;
    mark_price: string;
    market_value: string;
    unrealized_pnl: string;
  }[];
  as_of: string;
  source: string;
};

export type WorkspaceRuntime = {
  runtime_id: string;
  workspace_id: string;
  account_id: string;
  strategy_version_id: string;
  mode: "paper" | "live";
  state: "running" | "paused" | "stopped";
  revision: number;
  updated_at: string;
};

export async function workspaceRuntimes(): Promise<{
  runtimes: WorkspaceRuntime[];
  error: boolean;
}> {
  const session = await readSession();
  if (!session) return { runtimes: [], error: false };
  try {
    const response = await fetch(
      `${session.config.apiOrigin}/api/v1/workspaces/${session.workspaceId}/runtimes`,
      {
        cache: "no-store",
        redirect: "error",
        headers: { Cookie: `${session.name}=${session.cookie}` },
        signal: AbortSignal.timeout(10_000),
      },
    );
    if (!response.ok) throw Error("Runtimes unavailable");
    const runtimes = (await response.json()) as WorkspaceRuntime[];
    if (
      !Array.isArray(runtimes) ||
      runtimes.some(
        (runtime) =>
          typeof runtime.runtime_id !== "string" ||
          !["paper", "live"].includes(runtime.mode) ||
          !["running", "paused", "stopped"].includes(runtime.state),
      )
    )
      throw Error("Invalid runtime list");
    return { runtimes, error: false };
  } catch {
    return { runtimes: [], error: true };
  }
}

export async function controlWorkspaceRuntime(
  runtimeId: string,
  action: "pause" | "resume" | "stop" | "cancel_orders" | "close_positions",
  confirmation: string,
): Promise<"success" | "error"> {
  if (!/^[a-f0-9-]{36}$/i.test(runtimeId)) return "error";
  if (
    ["stop", "cancel_orders", "close_positions"].includes(action) &&
    confirmation !== `CONFIRM ${action.toUpperCase()}`
  )
    return "error";
  const session = await readSession();
  if (!session) return "error";
  try {
    const authHeaders = { Cookie: `${session.name}=${session.cookie}` };
    const csrfResponse = await fetch(
      `${session.config.apiOrigin}/auth/session`,
      {
        cache: "no-store",
        redirect: "error",
        headers: authHeaders,
        signal: AbortSignal.timeout(10_000),
      },
    );
    if (!csrfResponse.ok) return "error";
    const csrf = await csrfResponse.json();
    if (typeof csrf.csrf_token !== "string") return "error";
    const response = await fetch(
      `${session.config.apiOrigin}/api/v1/workspaces/${session.workspaceId}/runtimes/${runtimeId}/control`,
      {
        method: "POST",
        cache: "no-store",
        redirect: "error",
        headers: {
          ...authHeaders,
          "X-CSRF-Token": csrf.csrf_token,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ action, confirmation: confirmation || null }),
        signal: AbortSignal.timeout(10_000),
      },
    );
    return response.ok ? "success" : "error";
  } catch {
    return "error";
  }
}

export async function workspacePortfolioSummary(): Promise<{
  summary: WorkspacePortfolioSummary | null;
  error: boolean;
}> {
  const session = await readSession();
  if (!session) return { summary: null, error: false };
  try {
    const response = await fetch(
      `${session.config.apiOrigin}/api/v1/workspaces/${session.workspaceId}/portfolio`,
      {
        cache: "no-store",
        redirect: "error",
        headers: { Cookie: `${session.name}=${session.cookie}` },
        signal: AbortSignal.timeout(10_000),
      },
    );
    if (response.status === 404) return { summary: null, error: false };
    if (!response.ok) throw Error("Portfolio unavailable");
    const body = (await response.json()) as WorkspacePortfolioSummary;
    if (
      typeof body.equity !== "string" ||
      !Array.isArray(body.positions) ||
      typeof body.source !== "string"
    )
      throw Error("Invalid portfolio summary");
    return { summary: body, error: false };
  } catch {
    return { summary: null, error: true };
  }
}

export type WorkspaceHealthSummary = {
  collectionFailures: number | null;
  queueBacklog: number | null;
  qualityIssues: number | null;
  streams: {
    source: string;
    instrument_id: string;
    status: string;
    latest_lag_ms: number | null;
  }[];
  brokers: { adapter_id: string; name: string; health: string }[];
  streamsAvailable: boolean;
  brokersAvailable: boolean;
  error: boolean;
};

export type WorkspaceAlert = {
  event_id: string;
  source: string;
  event_type: string;
  severity: "info" | "warning" | "critical";
  status: "open" | "acknowledged" | "closed";
  title: string;
  message: string;
  resource_id: string | null;
  occurred_at: string;
  updated_at: string;
  assignee_id: string | null;
  silenced_until: string | null;
  context_snapshot: Record<string, unknown>;
};

export type AlertDispositionRow = {
  actor_id: string;
  action: string;
  note: string | null;
  silenced_until: string | null;
  occurred_at: string;
};

export type WorkspaceStrategy = {
  strategy_id: string;
  workspace_id: string;
  name: string;
  description: string;
  current_version: number;
  archived: boolean;
};

export type StrategyVersionInfo = {
  strategy_id: string;
  strategy_version_id: string;
  version: number;
  code_sha256: string;
  parameter_schema: Record<string, unknown>;
  dependencies_sha256: string;
};

export type StrategyLifecycleInfo = {
  state: string;
  updated_at: string;
  approval: {
    approval_id: string;
    approver_id: string;
    approved_at: string;
    paper_observations: number;
    reason: string;
  } | null;
  history: {
    transition_id: string;
    from_state: string;
    to_state: string;
    actor_id: string;
    occurred_at: string;
    reason: string;
  }[];
};

export type BacktestRunInfo = {
  run_id: string;
  strategy_version_id: string;
  dataset_id: string;
  manifest_id: string;
  created_at: string;
  start: string;
  end: string;
  initial_cash: string;
  currency: string;
  benchmark_id?: string | null;
  cost_model_version: string;
  status: "queued" | "running" | "cancelled" | "failed" | "completed";
  progress: number;
  result_object_key: string | null;
  error_code: string | null;
  revision: number;
};

export type BacktestDatasetInfo = {
  dataset_id: string;
  version: number;
  granularity: string;
  start: string;
  end: string;
  content_sha256: string;
  instrument_ids: string[];
};

export type BacktestReportInfo = {
  schema_version: number;
  workspace_id: string;
  dataset_id: string;
  strategy_version_id: string;
  currency: string;
  determinism: { engine: string; seed: number };
  equity_curve: {
    timestamp: string;
    cash: string;
    equity: string;
    drawdown: string;
    benchmark_equity: string | null;
    positions: {
      instrument_id: string;
      quantity: string;
      market_value: string;
    }[];
  }[];
  orders: {
    instrument_id: string;
    side: "buy" | "sell";
    quantity: string;
    filled_quantity: string | null;
    submitted_at: string;
    filled_at: string | null;
    price: string | null;
    status:
      | "filled"
      | "rejected"
      | "partially_filled_end_of_data"
      | "unfilled_end_of_data";
  }[];
  fills: {
    instrument_id: string;
    side: "buy" | "sell";
    filled_quantity: string;
    filled_at: string;
    price: string;
    fee: string;
  }[];
  summary: {
    initial_cash: string;
    final_equity: string;
    total_return: string;
    ending_drawdown: string;
    sample_count: number;
  };
  metrics: {
    max_drawdown: string;
    annualized_return: string | null;
    annualized_volatility: string | null;
    sharpe_ratio: string | null;
    sortino_ratio: string | null;
    benchmark_return: string | null;
    calmar_ratio: string | null;
    win_rate: string | null;
    profit_factor: string | null;
    turnover: string;
    average_exposure: string;
    max_exposure: string;
    trade_count: number;
    winning_trades: number;
    losing_trades: number;
  } | null;
};

export type BacktestManifestInfo = {
  manifest_id: string;
  dataset_sha256: string;
  code_sha256: string;
  engine_name: string;
  engine_version: string;
  adapter_version: string;
  environment_image_digest: string;
  execution_config_json: string;
  execution_config_sha256: string;
};

export type BacktestComparisonInfo = {
  schema_version: number;
  workspace_id: string;
  comparable: boolean;
  warnings: string[];
  input_differences: string[];
  runs: {
    run_id: string;
    dataset_id: string;
    strategy_version_id: string;
    start: string;
    end: string;
    currency: string;
    benchmark_id: string | null;
    execution_config_json: string;
    result: BacktestReportInfo;
  }[];
};

export type BacktestWalkForwardPlanInfo = {
  dataset_id: string;
  instrument_id: string;
  start: string;
  end: string;
  train_window_seconds: number;
  validation_window_seconds: number;
  step_seconds: number;
  windows: {
    train_start: string;
    train_end: string;
    validation_start: string;
    validation_end: string;
    train_samples: number;
    validation_samples: number;
  }[];
};

export async function createBacktestWalkForwardPlan(input: {
  datasetId: string;
  instrumentId: string;
  start: string;
  end: string;
  trainWindowSeconds: number;
  validationWindowSeconds: number;
  stepSeconds: number;
}): Promise<{ plan: BacktestWalkForwardPlanInfo | null; error?: string }> {
  const session = await readSession();
  if (!session) return { plan: null, error: "登录已失效，请重新登录" };
  try {
    const query = new URLSearchParams({
      dataset_id: input.datasetId,
      instrument_id: input.instrumentId,
      start: new Date(input.start).toISOString(),
      end: new Date(input.end).toISOString(),
      train_window_seconds: String(input.trainWindowSeconds),
      validation_window_seconds: String(input.validationWindowSeconds),
      step_seconds: String(input.stepSeconds),
    });
    const response = await fetch(
      `${session.config.apiOrigin}/api/v1/workspaces/${session.workspaceId}/backtests/walk-forward/plan?${query}`,
      {
        cache: "no-store",
        redirect: "error",
        headers: { Cookie: `${session.name}=${session.cookie}` },
        signal: AbortSignal.timeout(20_000),
      },
    );
    if (!response.ok)
      return {
        plan: null,
        error:
          response.status === 400
            ? "样本不足或时间范围与数据集不匹配"
            : "Walk-forward 计划暂不可用",
      };
    return { plan: (await response.json()) as BacktestWalkForwardPlanInfo };
  } catch {
    return { plan: null, error: "行情数据服务暂不可用" };
  }
}

export async function workspaceBacktestComparison(runIds: string[]): Promise<{
  comparison: BacktestComparisonInfo | null;
  error: boolean;
}> {
  const empty = { comparison: null, error: true };
  const session = await readSession();
  if (
    !session ||
    runIds.length < 2 ||
    runIds.length > 20 ||
    runIds.some((runId) => !/^[0-9a-f-]{36}$/i.test(runId))
  )
    return empty;
  try {
    const query = new URLSearchParams();
    runIds.forEach((runId) => query.append("run_id", runId));
    const response = await fetch(
      `${session.config.apiOrigin}/api/v1/workspaces/${session.workspaceId}/backtests/compare?${query}`,
      {
        cache: "no-store",
        redirect: "error",
        headers: { Cookie: `${session.name}=${session.cookie}` },
      },
    );
    if (!response.ok) return empty;
    return {
      comparison: (await response.json()) as BacktestComparisonInfo,
      error: false,
    };
  } catch {
    return empty;
  }
}

export async function workspaceStrategies(strategyId?: string): Promise<{
  strategies: WorkspaceStrategy[];
  selected: WorkspaceStrategy | null;
  versions: StrategyVersionInfo[];
  lifecycle: StrategyLifecycleInfo | null;
  code: string;
  dependencies: string;
  error: boolean;
}> {
  const empty = {
    strategies: [],
    selected: null,
    versions: [],
    lifecycle: null,
    code: "",
    dependencies: "{}",
    error: false,
  };
  const session = await readSession();
  if (!session) return empty;
  try {
    const root = `${session.config.apiOrigin}/api/v1/workspaces/${session.workspaceId}/strategies`;
    const headers = { Cookie: `${session.name}=${session.cookie}` };
    const response = await fetch(`${root}?limit=200`, {
      cache: "no-store",
      redirect: "error",
      headers,
    });
    if (!response.ok) throw Error("Strategies unavailable");
    const strategies = (await response.json()) as WorkspaceStrategy[];
    if (!Array.isArray(strategies)) throw Error("Invalid strategies");
    const selectedId =
      strategyId && /^[a-f0-9-]{36}$/i.test(strategyId)
        ? strategyId
        : undefined;
    const selected = selectedId
      ? (strategies.find((item) => item.strategy_id === selectedId) ?? null)
      : null;
    if (selectedId && !selected) throw Error("Strategy unavailable");
    if (!selected) return { ...empty, strategies };
    const versionsResponse = await fetch(
      `${root}/${selected.strategy_id}/versions`,
      { cache: "no-store", redirect: "error", headers },
    );
    if (!versionsResponse.ok) throw Error("Strategy versions unavailable");
    const versions = (await versionsResponse.json()) as StrategyVersionInfo[];
    if (!Array.isArray(versions)) throw Error("Invalid strategy versions");
    const current = versions.find(
      (item) => item.version === selected.current_version,
    );
    if (!current) throw Error("Current strategy version unavailable");
    const [sourceResponse, dependenciesResponse] = await Promise.all([
      fetch(
        `${root}/${selected.strategy_id}/versions/${current.version}/source`,
        { cache: "no-store", redirect: "error", headers },
      ),
      fetch(
        `${root}/${selected.strategy_id}/versions/${current.version}/dependencies`,
        { cache: "no-store", redirect: "error", headers },
      ),
    ]);
    const lifecycleResponse = await fetch(
      `${root}/${selected.strategy_id}/versions/${current.version}/lifecycle`,
      { cache: "no-store", redirect: "error", headers },
    );
    if (!sourceResponse.ok || !dependenciesResponse.ok || !lifecycleResponse.ok)
      throw Error("Strategy content unavailable");
    const source = await sourceResponse.json();
    const dependencyManifest = await dependenciesResponse.json();
    const lifecycle = (await lifecycleResponse.json()) as StrategyLifecycleInfo;
    if (
      typeof source.code !== "string" ||
      typeof dependencyManifest.dependencies !== "object"
    )
      throw Error("Invalid strategy content");
    return {
      strategies,
      selected,
      versions,
      lifecycle,
      code: source.code,
      dependencies: JSON.stringify(dependencyManifest.dependencies, null, 2),
      error: false,
    };
  } catch {
    return { ...empty, error: true };
  }
}

export async function validateStrategyVersion(
  strategyId: string,
  version: number,
): Promise<{ ok: boolean; error?: string }> {
  const session = await readSession();
  if (!session) return { ok: false, error: "登录已失效，请重新登录" };
  try {
    const authHeaders = { Cookie: `${session.name}=${session.cookie}` };
    const csrfResponse = await fetch(
      `${session.config.apiOrigin}/auth/session`,
      {
        cache: "no-store",
        redirect: "error",
        headers: authHeaders,
      },
    );
    if (!csrfResponse.ok) return { ok: false, error: "无法验证当前会话" };
    const csrf = await csrfResponse.json();
    const url = `${session.config.apiOrigin}/api/v1/workspaces/${session.workspaceId}/strategies/${strategyId}/versions/${version}/lifecycle/transitions`;
    const response = await fetch(url, {
      method: "POST",
      cache: "no-store",
      redirect: "error",
      headers: {
        ...authHeaders,
        "X-CSRF-Token": csrf.csrf_token,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        state: "validated",
        reason: "editor static validation",
      }),
      signal: AbortSignal.timeout(10_000),
    });
    return response.ok
      ? { ok: true }
      : {
          ok: false,
          error:
            response.status === 403
              ? "版本尚未保存，或当前角色无权校验"
              : "版本状态未能更新，请重新载入后重试",
        };
  } catch {
    return { ok: false, error: "策略服务暂不可用" };
  }
}

export async function workspaceBacktests(): Promise<{
  runs: BacktestRunInfo[];
  datasets: BacktestDatasetInfo[];
  strategies: WorkspaceStrategy[];
  error: boolean;
}> {
  const empty = { runs: [], datasets: [], strategies: [], error: false };
  const session = await readSession();
  if (!session) return empty;
  try {
    const headers = { Cookie: `${session.name}=${session.cookie}` };
    const root = `${session.config.apiOrigin}/api/v1/workspaces/${session.workspaceId}`;
    const [runsResponse, datasetsResponse, strategiesResponse] =
      await Promise.all([
        fetch(`${root}/backtests?limit=200`, {
          cache: "no-store",
          redirect: "error",
          headers,
        }),
        fetch(`${root}/backtests/datasets?limit=200`, {
          cache: "no-store",
          redirect: "error",
          headers,
        }),
        fetch(`${root}/strategies?limit=200`, {
          cache: "no-store",
          redirect: "error",
          headers,
        }),
      ]);
    if (!runsResponse.ok || !datasetsResponse.ok || !strategiesResponse.ok)
      throw Error("Backtests unavailable");
    const [runs, datasets, strategies] = await Promise.all([
      runsResponse.json() as Promise<BacktestRunInfo[]>,
      datasetsResponse.json() as Promise<BacktestDatasetInfo[]>,
      strategiesResponse.json() as Promise<WorkspaceStrategy[]>,
    ]);
    if (
      !Array.isArray(runs) ||
      !Array.isArray(datasets) ||
      !Array.isArray(strategies)
    )
      throw Error("Invalid backtest response");
    return { runs, datasets, strategies, error: false };
  } catch {
    return { ...empty, error: true };
  }
}

export async function workspaceBacktestReport(runId: string): Promise<{
  run: BacktestRunInfo | null;
  report: BacktestReportInfo | null;
  manifest: BacktestManifestInfo | null;
  error: boolean;
}> {
  const empty = { run: null, report: null, manifest: null, error: true };
  const session = await readSession();
  if (!session || !/^[0-9a-f-]{36}$/i.test(runId)) return empty;
  const root = `${session.config.apiOrigin}/api/v1/workspaces/${session.workspaceId}/backtests/${runId}`;
  try {
    const headers = { Cookie: `${session.name}=${session.cookie}` };
    const runResponse = await fetch(root, {
      cache: "no-store",
      redirect: "error",
      headers,
    });
    if (!runResponse.ok) return empty;
    const run = (await runResponse.json()) as BacktestRunInfo;
    if (run.status !== "completed")
      return { run, report: null, manifest: null, error: false };
    const [reportResponse, manifestResponse] = await Promise.all([
      fetch(`${root}/report`, {
        cache: "no-store",
        redirect: "error",
        headers,
      }),
      fetch(`${root}/manifest`, {
        cache: "no-store",
        redirect: "error",
        headers,
      }),
    ]);
    if (!reportResponse.ok || !manifestResponse.ok)
      return { run, report: null, manifest: null, error: true };
    const [report, manifest] = await Promise.all([
      reportResponse.json() as Promise<BacktestReportInfo>,
      manifestResponse.json() as Promise<BacktestManifestInfo>,
    ]);
    if (
      report.dataset_id !== run.dataset_id ||
      report.strategy_version_id !== run.strategy_version_id
    )
      return empty;
    return { run, report, manifest, error: false };
  } catch {
    return empty;
  }
}

export async function createBacktest(input: {
  strategyId: string;
  version: number;
  datasetId: string;
  start: string;
  end: string;
  initialCash: string;
  currency: string;
  costModelVersion: string;
  parameters: Record<string, unknown>;
  executionModel: Record<string, unknown>;
  benchmarkId?: string;
}): Promise<{ ok: boolean; runId?: string; error?: string }> {
  const session = await readSession();
  if (!session) return { ok: false, error: "登录已失效，请重新登录" };
  try {
    const authHeaders = { Cookie: `${session.name}=${session.cookie}` };
    const csrfResponse = await fetch(
      `${session.config.apiOrigin}/auth/session`,
      {
        cache: "no-store",
        redirect: "error",
        headers: authHeaders,
      },
    );
    if (!csrfResponse.ok) return { ok: false, error: "无法验证当前会话" };
    const csrf = await csrfResponse.json();
    const response = await fetch(
      `${session.config.apiOrigin}/api/v1/workspaces/${session.workspaceId}/backtests`,
      {
        method: "POST",
        cache: "no-store",
        redirect: "error",
        headers: {
          ...authHeaders,
          "X-CSRF-Token": csrf.csrf_token,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          strategy_id: input.strategyId,
          strategy_version: input.version,
          dataset_id: input.datasetId,
          start: new Date(input.start).toISOString(),
          end: new Date(input.end).toISOString(),
          initial_cash: input.initialCash,
          currency: input.currency,
          cost_model_version: input.costModelVersion,
          parameters: input.parameters,
          execution_model: input.executionModel,
          benchmark_id: input.benchmarkId,
          random_seed: 0,
        }),
        signal: AbortSignal.timeout(20_000),
      },
    );
    if (!response.ok)
      return {
        ok: false,
        error:
          response.status === 403
            ? "版本尚未通过静态校验，或当前角色无权回测"
            : response.status === 400
              ? "请检查回测区间、资金和输入参数"
              : "回测任务创建失败",
      };
    const result = await response.json();
    return typeof result.run_id === "string"
      ? { ok: true, runId: result.run_id }
      : { ok: false, error: "回测服务响应无效" };
  } catch {
    return { ok: false, error: "回测服务暂不可用" };
  }
}

export async function cancelBacktest(
  runId: string,
): Promise<{ ok: boolean; error?: string }> {
  const session = await readSession();
  if (!session) return { ok: false, error: "登录已失效，请重新登录" };
  try {
    const authHeaders = { Cookie: `${session.name}=${session.cookie}` };
    const csrfResponse = await fetch(
      `${session.config.apiOrigin}/auth/session`,
      {
        cache: "no-store",
        redirect: "error",
        headers: authHeaders,
      },
    );
    if (!csrfResponse.ok) return { ok: false, error: "无法验证当前会话" };
    const csrf = await csrfResponse.json();
    const response = await fetch(
      `${session.config.apiOrigin}/api/v1/workspaces/${session.workspaceId}/backtests/${runId}/cancel`,
      {
        method: "POST",
        cache: "no-store",
        redirect: "error",
        headers: { ...authHeaders, "X-CSRF-Token": csrf.csrf_token },
        body: "{}",
        signal: AbortSignal.timeout(10_000),
      },
    );
    return response.ok
      ? { ok: true }
      : {
          ok: false,
          error:
            response.status === 409 ? "任务已结束，不能取消" : "取消请求失败",
        };
  } catch {
    return { ok: false, error: "回测服务暂不可用" };
  }
}

export async function persistStrategy(input: {
  strategyId?: string;
  name?: string;
  description?: string;
  code: string;
  parameterSchema: Record<string, unknown>;
  dependencies: Record<string, string>;
}): Promise<{
  ok: boolean;
  strategyId?: string;
  version?: number;
  error?: string;
}> {
  const session = await readSession();
  if (!session) return { ok: false, error: "登录已失效，请重新登录" };
  try {
    const authHeaders = { Cookie: `${session.name}=${session.cookie}` };
    const csrfResponse = await fetch(
      `${session.config.apiOrigin}/auth/session`,
      { cache: "no-store", redirect: "error", headers: authHeaders },
    );
    if (!csrfResponse.ok) return { ok: false, error: "无法验证当前会话" };
    const csrf = await csrfResponse.json();
    if (typeof csrf.csrf_token !== "string")
      return { ok: false, error: "无法验证当前会话" };
    const root = `${session.config.apiOrigin}/api/v1/workspaces/${session.workspaceId}/strategies`;
    const url = input.strategyId
      ? `${root}/${input.strategyId}/versions`
      : root;
    const payload = input.strategyId
      ? {
          code: input.code,
          parameter_schema: input.parameterSchema,
          dependencies: input.dependencies,
        }
      : {
          name: input.name,
          description: input.description ?? "",
          code: input.code,
          parameter_schema: input.parameterSchema,
          dependencies: input.dependencies,
        };
    const response = await fetch(url, {
      method: "POST",
      cache: "no-store",
      redirect: "error",
      headers: {
        ...authHeaders,
        "X-CSRF-Token": csrf.csrf_token,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(20_000),
    });
    if (!response.ok)
      return {
        ok: false,
        error:
          response.status === 400
            ? "策略或参数 schema 无效"
            : response.status === 403
              ? "当前角色无权编辑策略"
              : "策略保存失败",
      };
    const result = await response.json();
    const saved = input.strategyId ? result : result.strategy;
    const version = input.strategyId ? result.version : result.version?.version;
    return typeof saved?.strategy_id === "string" && typeof version === "number"
      ? { ok: true, strategyId: saved.strategy_id, version }
      : { ok: false, error: "策略响应无效" };
  } catch {
    return { ok: false, error: "策略服务暂不可用" };
  }
}

export async function checkStrategyParameters(
  strategyId: string,
  version: number,
  parameters: Record<string, unknown>,
): Promise<{
  ok: boolean;
  parameters?: Record<string, unknown>;
  error?: string;
}> {
  if (
    !/^[a-f0-9-]{36}$/i.test(strategyId) ||
    !Number.isInteger(version) ||
    version < 1
  )
    return { ok: false, error: "策略版本无效" };
  const session = await readSession();
  if (!session) return { ok: false, error: "登录已失效，请重新登录" };
  try {
    const url = `${session.config.apiOrigin}/api/v1/workspaces/${session.workspaceId}/strategies/${strategyId}/versions/${version}/parameters/validate`;
    const authHeaders = { Cookie: `${session.name}=${session.cookie}` };
    const csrfResponse = await fetch(
      `${session.config.apiOrigin}/auth/session`,
      {
        cache: "no-store",
        redirect: "error",
        headers: authHeaders,
      },
    );
    if (!csrfResponse.ok) return { ok: false, error: "无法验证当前会话" };
    const csrf = await csrfResponse.json();
    if (typeof csrf.csrf_token !== "string")
      return { ok: false, error: "无法验证当前会话" };
    const response = await fetch(url, {
      method: "POST",
      cache: "no-store",
      redirect: "error",
      headers: {
        ...authHeaders,
        "X-CSRF-Token": csrf.csrf_token,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ parameters }),
      signal: AbortSignal.timeout(10_000),
    });
    if (!response.ok)
      return {
        ok: false,
        error:
          response.status === 400
            ? "参数类型或取值范围不符合此版本 schema"
            : response.status === 404
              ? "策略版本已不存在"
              : "参数校验暂不可用",
      };
    const body = await response.json();
    return body && typeof body.parameters === "object"
      ? { ok: true, parameters: body.parameters }
      : { ok: false, error: "校验结果无效" };
  } catch {
    return { ok: false, error: "参数校验暂不可用" };
  }
}

export type StrategyStaticDiagnostic = {
  code: string;
  severity: "error" | "warning";
  message: string;
  line: number | null;
  column: number | null;
};

export async function checkStrategySource(
  code: string,
  dependencies: Record<string, string>,
): Promise<{
  ok: boolean;
  passed?: boolean;
  astNodeCount?: number;
  diagnostics?: StrategyStaticDiagnostic[];
  error?: string;
}> {
  const session = await readSession();
  if (!session) return { ok: false, error: "登录已失效，请重新登录" };
  try {
    const authHeaders = { Cookie: `${session.name}=${session.cookie}` };
    const csrfResponse = await fetch(
      `${session.config.apiOrigin}/auth/session`,
      { cache: "no-store", redirect: "error", headers: authHeaders },
    );
    if (!csrfResponse.ok) return { ok: false, error: "无法验证当前会话" };
    const csrf = await csrfResponse.json();
    if (typeof csrf.csrf_token !== "string")
      return { ok: false, error: "无法验证当前会话" };
    const response = await fetch(
      `${session.config.apiOrigin}/api/v1/workspaces/${session.workspaceId}/strategies/static-validation`,
      {
        method: "POST",
        cache: "no-store",
        redirect: "error",
        headers: {
          ...authHeaders,
          "X-CSRF-Token": csrf.csrf_token,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ code, dependencies }),
        signal: AbortSignal.timeout(10_000),
      },
    );
    if (!response.ok)
      return {
        ok: false,
        error:
          response.status === 403
            ? "当前角色无权执行策略校验"
            : "策略静态校验暂不可用",
      };
    const result = await response.json();
    return typeof result.passed === "boolean" &&
      Array.isArray(result.diagnostics)
      ? {
          ok: true,
          passed: result.passed,
          astNodeCount: result.ast_node_count,
          diagnostics: result.diagnostics,
        }
      : { ok: false, error: "策略校验响应无效" };
  } catch {
    return { ok: false, error: "策略静态校验暂不可用" };
  }
}

export async function workspaceAlerts(eventId?: string): Promise<{
  events: WorkspaceAlert[];
  selected: WorkspaceAlert | null;
  history: AlertDispositionRow[];
  error: boolean;
}> {
  const session = await readSession();
  if (!session)
    return { events: [], selected: null, history: [], error: false };
  try {
    const base = `${session.config.apiOrigin}/api/v1/workspaces/${session.workspaceId}/alert-rules/events`;
    const headers = { Cookie: `${session.name}=${session.cookie}` };
    const listResponse = await fetch(`${base}?limit=200`, {
      cache: "no-store",
      redirect: "error",
      headers,
    });
    if (!listResponse.ok) throw Error("Alerts unavailable");
    const rows = (await listResponse.json()) as WorkspaceAlert[];
    if (!Array.isArray(rows)) throw Error("Invalid alerts");
    const selectedId =
      eventId && /^[a-f0-9-]{36}$/i.test(eventId) ? eventId : rows[0]?.event_id;
    if (!selectedId)
      return { events: rows, selected: null, history: [], error: false };
    const [detailResponse, historyResponse] = await Promise.all([
      fetch(`${base}/${selectedId}`, {
        cache: "no-store",
        redirect: "error",
        headers,
      }),
      fetch(`${base}/${selectedId}/history`, {
        cache: "no-store",
        redirect: "error",
        headers,
      }),
    ]);
    if (!detailResponse.ok || !historyResponse.ok)
      throw Error("Alert detail unavailable");
    const selected = (await detailResponse.json()) as WorkspaceAlert;
    const history = (await historyResponse.json()) as AlertDispositionRow[];
    if (!Array.isArray(history)) throw Error("Invalid alert history");
    return { events: rows, selected, history, error: false };
  } catch {
    return { events: [], selected: null, history: [], error: true };
  }
}

export async function workspaceHealthSummary(): Promise<WorkspaceHealthSummary | null> {
  const session = await readSession();
  if (!session) return null;
  const headers = { Cookie: `${session.name}=${session.cookie}` };
  const base = `${session.config.apiOrigin}/api/v1/workspaces/${session.workspaceId}`;
  const requests = await Promise.allSettled([
    workspaceData(),
    fetch(`${base}/market-streams?limit=200`, {
      cache: "no-store",
      redirect: "error",
      headers,
      signal: AbortSignal.timeout(10_000),
    }).then(async (response) => {
      if (!response.ok) throw Error("Market stream health unavailable");
      return response.json();
    }),
    workspaceAdapters(),
  ]);
  const data = requests[0].status === "fulfilled" ? requests[0].value : null;
  const streamRows =
    requests[1].status === "fulfilled" && Array.isArray(requests[1].value)
      ? requests[1].value
      : null;
  const adapterRows =
    requests[2].status === "fulfilled" && Array.isArray(requests[2].value)
      ? requests[2].value
      : null;
  if (!data && !streamRows && !adapterRows)
    return {
      collectionFailures: null,
      queueBacklog: null,
      qualityIssues: null,
      streams: [],
      brokers: [],
      streamsAvailable: false,
      brokersAvailable: false,
      error: true,
    };
  const tasks = data?.tasks ?? [];
  const dataAvailable = Boolean(data && !data.error);
  return {
    collectionFailures: dataAvailable
      ? tasks.filter((task) => task.status === "failed").length
      : null,
    queueBacklog: dataAvailable
      ? tasks.filter((task) => ["queued", "waiting"].includes(task.status))
          .length
      : null,
    qualityIssues: dataAvailable ? data!.issues.length : null,
    streams:
      streamRows?.flatMap((item) =>
        item &&
        typeof item.source === "string" &&
        typeof item.instrument_id === "string" &&
        typeof item.status === "string"
          ? [
              {
                source: item.source,
                instrument_id: item.instrument_id,
                status: item.status,
                latest_lag_ms:
                  typeof item.latest_lag_ms === "number"
                    ? item.latest_lag_ms
                    : null,
              },
            ]
          : [],
      ) ?? [],
    brokers:
      adapterRows?.flatMap((item) =>
        item &&
        item.kind === "broker" &&
        typeof item.adapter_id === "string" &&
        typeof item.name === "string" &&
        typeof item.health === "string"
          ? [
              {
                adapter_id: item.adapter_id,
                name: item.name,
                health: item.health,
              },
            ]
          : [],
      ) ?? [],
    streamsAvailable: streamRows !== null,
    brokersAvailable: adapterRows !== null,
    error:
      !dataAvailable ||
      requests[1].status === "rejected" ||
      requests[2].status === "rejected" ||
      !streamRows ||
      !adapterRows,
  };
}

export async function workspaceMarketStream(): Promise<{
  workspaceId: string;
  websocketOrigin: string;
} | null> {
  const session = await readSession();
  if (!session?.config.publicApiOrigin) return null;
  const endpoint = new URL(session.config.publicApiOrigin);
  endpoint.protocol = endpoint.protocol === "https:" ? "wss:" : "ws:";
  return {
    workspaceId: session.workspaceId,
    websocketOrigin: endpoint.origin,
  };
}

export async function workspaceAdapters(): Promise<unknown> {
  const session = await readSession();
  if (!session) throw new Error("Workspace unavailable");
  const response = await fetch(
    session.config.apiOrigin +
      "/api/v1/workspaces/" +
      session.workspaceId +
      "/adapters",
    {
      cache: "no-store",
      redirect: "error",
      headers: { Cookie: `${session.name}=${session.cookie}` },
      signal: AbortSignal.timeout(10_000),
    },
  );
  if (!response.ok) throw new Error("Capabilities unavailable");
  return response.json();
}

export type DataTask = {
  task_id: string;
  source: string;
  instrument_id: string;
  granularity: string;
  start: string;
  end: string;
  status: string;
  next_attempt_at: string;
  page_count: number;
  collection_complete: boolean;
  failures: number;
  last_error_code: string | null;
  dataset_id: string | null;
  download_group_id?: string | null;
  part_index?: number;
  part_count?: number;
};
export type DataBatch = {
  batch_id: string;
  source: string;
  ingestion_task_id: string;
  instrument_id: string;
  granularity: string;
  collected_at: string;
  content_sha256: string;
  record_count: number;
};
export type DataIssue = {
  issue_id: string;
  dataset_id: string;
  code: string;
  severity: string;
  instrument_id: string;
  granularity: string;
  starts_at: string;
  ends_at: string;
  batch_ids: string[];
  evidence: Record<string, unknown>;
  detected_at: string;
  status: string;
  claimed_by: string | null;
  closed_by: string | null;
  closed_at: string | null;
};

export type MarketInstrument = {
  available_granularities?: string[];
  instrument_id: string;
  name: string;
  market: string;
  exchange: string;
  currency: string;
  price_precision: number;
};
export type MarketSnapshot = {
  instrument_id: string;
  dataset_id: string | null;
  granularity: string | null;
  available_granularities?: string[];
  latest: { timestamp: string; close: string; volume: string } | null;
  previous: { timestamp: string; close: string; volume: string } | null;
};
export type MarketChartBar = {
  timestamp: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
};
export type MarketChartIndicators = Record<
  | "ma"
  | "ema"
  | "macd"
  | "macd_signal"
  | "macd_histogram"
  | "rsi"
  | "bollinger_upper"
  | "bollinger_middle"
  | "bollinger_lower"
  | "atr",
  (string | null)[]
>;

export async function workspaceMarketDatasets(): Promise<{
  datasets: BacktestDatasetInfo[];
  error: boolean;
}> {
  const session = await readSession();
  if (!session) return { datasets: [], error: true };
  try {
    const response = await fetch(
      `${session.config.apiOrigin}/api/v1/workspaces/${session.workspaceId}/backtests/datasets?limit=200`,
      {
        cache: "no-store",
        redirect: "error",
        headers: { Cookie: `${session.name}=${session.cookie}` },
        signal: AbortSignal.timeout(10_000),
      },
    );
    if (!response.ok) throw Error("Published datasets unavailable");
    const datasets = (await response.json()) as BacktestDatasetInfo[];
    if (
      !Array.isArray(datasets) ||
      datasets.some(
        (dataset) =>
          !dataset ||
          typeof dataset.dataset_id !== "string" ||
          typeof dataset.granularity !== "string" ||
          !Array.isArray(dataset.instrument_ids),
      )
    )
      throw Error("Invalid published dataset response");
    return { datasets, error: false };
  } catch {
    return { datasets: [], error: true };
  }
}

export async function workspaceMarketInstruments(
  selectedInstrumentId?: string,
  { includeDownloads = true }: { includeDownloads?: boolean } = {},
): Promise<{
  instruments: MarketInstrument[];
  datasets: BacktestDatasetInfo[];
  datasetsError: boolean;
  error: boolean;
} | null> {
  const session = await readSession();
  if (!session) return null;
  try {
    const response = await fetch(
      `${session.config.apiOrigin}/api/v1/workspaces/${session.workspaceId}/instruments?limit=200&enabled=true`,
      {
        cache: "no-store",
        redirect: "error",
        headers: { Cookie: `${session.name}=${session.cookie}` },
        signal: AbortSignal.timeout(10_000),
      },
    );
    if (!response.ok) throw Error("Instrument catalog unavailable");
    const body = await response.json();
    if (!Array.isArray(body.instruments))
      throw Error("Invalid instrument catalog");
    const [research, published] = includeDownloads
      ? await Promise.all([
          workspaceResearchDatasets(),
          workspaceMarketDatasets(),
        ])
      : [
          { datasets: [], available: false },
          { datasets: [], error: false },
        ];
    const instruments: MarketInstrument[] = body.instruments.map(
      (item: MarketInstrument) => ({ ...item, available_granularities: [] }),
    );
    const known = new Map(
      instruments.map((item) => [item.instrument_id, item]),
    );
    // Published market datasets are the authoritative downloaded-data catalog.
    // Add their Instruments even when the master catalog is capped or stale.
    for (const dataset of published.datasets) {
      for (const id of dataset.instrument_ids) {
        let instrument = known.get(id);
        if (!instrument) {
          instrument = {
            instrument_id: id,
            name: "已下载历史数据",
            market: "CN",
            exchange: id.match(/\.(SH|SZ|BJ)$/)?.[1] ?? "CN",
            currency: "CNY",
            price_precision: 2,
            available_granularities: [],
          };
          instruments.push(instrument);
          known.set(id, instrument);
        }
        if (
          ["1m", "5m", "15m", "30m", "1h", "1d"].includes(
            dataset.granularity,
          ) &&
          !instrument.available_granularities?.includes(dataset.granularity)
        )
          instrument.available_granularities?.push(dataset.granularity);
      }
    }
    for (const dataset of research.datasets) {
      for (const id of dataset.instrument_ids) {
        let instrument = known.get(id);
        if (!instrument) {
          instrument = {
            instrument_id: id,
            name: "已下载历史数据",
            market: "CN",
            exchange: id.match(/\.(SH|SZ|BJ)$/)?.[1] ?? "CN",
            currency: "CNY",
            price_precision: 2,
            available_granularities: [],
          };
          instruments.push(instrument);
          known.set(id, instrument);
        }
        if (
          !["1m", "5m", "15m", "30m", "1h", "1d"].includes(dataset.granularity)
        )
          continue;
        if (
          !instrument.available_granularities?.includes(dataset.granularity)
        ) {
          instrument.available_granularities?.push(dataset.granularity);
        }
      }
    }
    if (
      selectedInstrumentId &&
      /^[A-Za-z0-9_.:-]{1,128}$/.test(selectedInstrumentId) &&
      !known.has(selectedInstrumentId)
    ) {
      instruments.push({
        instrument_id: selectedInstrumentId,
        name: "股票池标的",
        market: "CN",
        exchange: selectedInstrumentId.match(/\.(SH|SZ|BJ)$/)?.[1] ?? "CN",
        currency: "CNY",
        price_precision: 2,
        available_granularities: [],
      });
    }
    const names = await workspaceInstrumentNames(
      instruments.map((item) => item.instrument_id),
    );
    for (const instrument of instruments) {
      if (
        names[instrument.instrument_id] &&
        names[instrument.instrument_id] !== "名称未核验"
      )
        instrument.name = names[instrument.instrument_id];
    }
    return {
      instruments,
      datasets: published.datasets,
      datasetsError: published.error,
      error: false,
    };
  } catch {
    return {
      instruments: [],
      datasets: [],
      datasetsError: true,
      error: true,
    };
  }
}

export async function workspaceCommandInstruments(query: string): Promise<{
  instruments: MarketInstrument[];
  error: boolean;
}> {
  if (
    typeof query !== "string" ||
    query.trim().length < 1 ||
    query.length > 128
  )
    return { instruments: [], error: false };
  const catalog = await workspaceMarketInstruments(undefined, {
    includeDownloads: false,
  });
  if (!catalog || catalog.error) return { instruments: [], error: true };
  const normalized = query.trim().toLocaleLowerCase();
  return {
    instruments: catalog.instruments
      .filter((item) =>
        [item.instrument_id, item.name, item.market, item.exchange].some(
          (value) => value.toLocaleLowerCase().includes(normalized),
        ),
      )
      .slice(0, 10),
    error: false,
  };
}

export async function workspaceMarketSnapshots(
  instrumentIds: string[],
): Promise<{ snapshots: MarketSnapshot[]; error: boolean }> {
  const session = await readSession();
  if (
    !session ||
    !Array.isArray(instrumentIds) ||
    instrumentIds.length > 50 ||
    instrumentIds.some(
      (id) =>
        typeof id !== "string" ||
        id.trim().length === 0 ||
        id.length > 256 ||
        /[\u0000-\u001f\u007f]/.test(id),
    )
  )
    return { snapshots: [], error: true };
  if (instrumentIds.length === 0) return { snapshots: [], error: false };
  try {
    const query = new URLSearchParams();
    for (const id of instrumentIds) query.append("instrument_id", id);
    const response = await fetch(
      `${session.config.apiOrigin}/api/v1/workspaces/${session.workspaceId}/market-watch?${query}`,
      {
        cache: "no-store",
        redirect: "error",
        headers: { Cookie: `${session.name}=${session.cookie}` },
        signal: AbortSignal.timeout(10_000),
      },
    );
    if (!response.ok) throw Error("Market watch unavailable");
    const snapshots = await response.json();
    if (!Array.isArray(snapshots)) throw Error("Invalid market snapshot");
    return { snapshots, error: false };
  } catch {
    return { snapshots: [], error: true };
  }
}

export async function workspaceMarketChart(
  instrumentId: string,
  granularity: string,
  days: number,
  datasetId?: string,
): Promise<{
  bars: MarketChartBar[];
  indicators: MarketChartIndicators | null;
  error: "none" | "missing" | "unavailable";
}> {
  const session = await readSession();
  if (
    !session ||
    typeof instrumentId !== "string" ||
    instrumentId.trim().length === 0 ||
    instrumentId.length > 256 ||
    /[\u0000-\u001f\u007f]/.test(instrumentId) ||
    !["1m", "5m", "15m", "30m", "1h", "1d"].includes(granularity) ||
    ![1, 7, 30].includes(days) ||
    (datasetId !== undefined &&
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
        datasetId,
      ))
  )
    return { bars: [], indicators: null, error: "unavailable" };
  try {
    const query = new URLSearchParams({
      instrument_id: instrumentId,
      granularity,
      days: String(days),
    });
    if (datasetId) query.set("dataset_id", datasetId);
    const response = await fetch(
      `${session.config.apiOrigin}/api/v1/workspaces/${session.workspaceId}/market-watch/chart?${query}`,
      {
        cache: "no-store",
        redirect: "error",
        headers: { Cookie: `${session.name}=${session.cookie}` },
        signal: AbortSignal.timeout(10_000),
      },
    );
    if (response.status === 404)
      return { bars: [], indicators: null, error: "missing" };
    if (!response.ok) throw Error("Market chart unavailable");
    const body = await response.json();
    if (!Array.isArray(body.bars) || !body.indicators)
      throw Error("Invalid market chart");
    return { bars: body.bars, indicators: body.indicators, error: "none" };
  } catch {
    return { bars: [], indicators: null, error: "unavailable" };
  }
}

export async function workspaceData(): Promise<{
  workspaceId: string;
  tasks: DataTask[];
  batches: DataBatch[];
  issues: DataIssue[];
  sources: string[];
  sourceOptions: import("../data/history-form").SourceOption[];
  error: boolean;
  maxDownloadDays: number | null;
} | null> {
  const session = await readSession();
  if (!session) return null;
  try {
    const root = `${session.config.apiOrigin}/api/v1/workspaces/${session.workspaceId}/data`;
    const headers = { Cookie: `${session.name}=${session.cookie}` };
    const responses = await Promise.all(
      [
        "tasks",
        "batches",
        "quality-issues",
        "sources",
        "download-policy",
        "source-options",
      ].map((path) =>
        fetch(`${root}/${path}`, {
          cache: "no-store",
          redirect: "error",
          headers,
          signal: AbortSignal.timeout(10_000),
        }),
      ),
    );
    if (responses.some((response) => !response.ok))
      throw Error("Data unavailable");
    const [tasks, batches, issues, sources, policy, sourceOptions] =
      await Promise.all(responses.map((response) => response.json()));
    if (
      !Array.isArray(tasks) ||
      !Array.isArray(batches) ||
      !Array.isArray(issues) ||
      !Array.isArray(sources) ||
      sources.some((source) => typeof source !== "string") ||
      !Array.isArray(sourceOptions) ||
      !policy ||
      !(
        policy.max_days === null ||
        (Number.isInteger(policy.max_days) && policy.max_days > 0)
      )
    )
      throw Error("Invalid data");
    return {
      workspaceId: session.workspaceId,
      tasks,
      batches,
      issues,
      sources,
      sourceOptions,
      maxDownloadDays: policy.max_days,
      error: false,
    };
  } catch {
    return {
      workspaceId: session.workspaceId,
      tasks: [],
      batches: [],
      issues: [],
      sources: [],
      sourceOptions: [],
      maxDownloadDays: null,
      error: true,
    };
  }
}

async function mutateData(
  path: string,
  payload?: Record<string, string>,
): Promise<
  | "success"
  | "error"
  | "permission"
  | "invalid"
  | "unavailable"
  | "complete"
  | "partial"
  | "range"
> {
  const session = await readSession();
  if (!session) return "permission";
  try {
    const csrfResponse = await fetch(
      session.config.apiOrigin + "/auth/session",
      {
        cache: "no-store",
        redirect: "error",
        headers: { Cookie: `${session.name}=${session.cookie}` },
        signal: AbortSignal.timeout(10_000),
      },
    );
    if (!csrfResponse.ok)
      return csrfResponse.status === 401 || csrfResponse.status === 403
        ? "permission"
        : "error";
    const csrf = await csrfResponse.json();
    if (typeof csrf.csrf_token !== "string") return "error";
    const response = await fetch(
      `${session.config.apiOrigin}/api/v1/workspaces/${session.workspaceId}/data/${path}`,
      {
        method: "POST",
        cache: "no-store",
        redirect: "error",
        headers: {
          Cookie: `${session.name}=${session.cookie}`,
          "X-CSRF-Token": csrf.csrf_token,
          ...(payload ? { "Content-Type": "application/json" } : {}),
        },
        ...(payload ? { body: JSON.stringify(payload) } : {}),
        signal: AbortSignal.timeout(10_000),
      },
    );
    if (response.ok) return "success";
    if (response.status === 401 || response.status === 403) return "permission";
    if (response.status === 400 || response.status === 422) {
      const body = await response.json();
      return body.detail?.code === "download_range_exceeded"
        ? "range"
        : "invalid";
    }
    if (response.status === 503) return "unavailable";
    if (response.status === 409 && path === "tasks") {
      const body = await response.json();
      if (body.detail?.code === "collection_choice_required")
        return body.detail.status === "complete" ? "complete" : "partial";
    }
    return "error";
  } catch {
    return "error";
  }
}

export async function handleAlert(
  eventId: string,
  action: "claim" | "acknowledge" | "silence" | "close" | "note",
  payload: { note?: string; silence_minutes?: number } = {},
): Promise<"success" | "error"> {
  if (!/^[a-f0-9-]{36}$/i.test(eventId)) return "error";
  const session = await readSession();
  if (!session) return "error";
  try {
    const authHeaders = { Cookie: `${session.name}=${session.cookie}` };
    const csrfResponse = await fetch(
      `${session.config.apiOrigin}/auth/session`,
      {
        cache: "no-store",
        redirect: "error",
        headers: authHeaders,
        signal: AbortSignal.timeout(10_000),
      },
    );
    if (!csrfResponse.ok) return "error";
    const csrf = await csrfResponse.json();
    if (typeof csrf.csrf_token !== "string") return "error";
    const body: Record<string, unknown> = {};
    if (payload.note) body.note = payload.note;
    if (action === "silence") {
      const minutes = payload.silence_minutes;
      if (!minutes || minutes < 1 || minutes > 43200) return "error";
      body.silenced_until = new Date(
        Date.now() + minutes * 60_000,
      ).toISOString();
    }
    const response = await fetch(
      `${session.config.apiOrigin}/api/v1/workspaces/${session.workspaceId}/alert-rules/events/${eventId}/actions/${action}`,
      {
        method: "POST",
        cache: "no-store",
        redirect: "error",
        headers: {
          ...authHeaders,
          "X-CSRF-Token": csrf.csrf_token,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(10_000),
      },
    );
    return response.ok ? "success" : "error";
  } catch {
    return "error";
  }
}

export async function rerunDataTask(taskId: string) {
  if (!/^[a-f0-9-]{36}$/i.test(taskId)) return "error" as const;
  return mutateData(`tasks/${taskId}/rerun`);
}
export async function createTickDataTask(input: {
  source: string;
  authorization_id: string;
  instrument_id: string;
  start: string;
  end: string;
  mode?: string;
  granularity?: string;
}) {
  if (
    !input.source.trim() ||
    !input.authorization_id.trim() ||
    !input.instrument_id.trim() ||
    !Number.isFinite(Date.parse(input.start)) ||
    !Number.isFinite(Date.parse(input.end)) ||
    Date.parse(input.start) >= Date.parse(input.end)
  )
    return "invalid" as const;
  if (
    (input.granularity ?? "tick") === "tick" &&
    Date.parse(input.start) < Date.parse("2026-08-12T16:00:00Z")
  )
    return "archive" as const;
  return mutateData("tasks", input);
}
export async function claimDataIssue(issueId: string) {
  if (!/^[a-f0-9-]{36}$/i.test(issueId)) return "error" as const;
  return mutateData(`quality-issues/${issueId}/claim`);
}
export async function closeDataIssue(issueId: string) {
  if (!/^[a-f0-9-]{36}$/i.test(issueId)) return "error" as const;
  return mutateData(`quality-issues/${issueId}/close`);
}

export async function currentIdentity(): Promise<{
  authenticated: boolean;
  workspace: PublicWorkspace | null;
}> {
  const session = await readSession();
  if (!session) return { authenticated: false, workspace: null };
  try {
    const response = await fetch(
      session.config.apiOrigin + "/api/v1/workspaces/" + session.workspaceId,
      {
        cache: "no-store",
        redirect: "error",
        headers: { Cookie: `${session.name}=${session.cookie}` },
        signal: AbortSignal.timeout(10_000),
      },
    );
    if (!response.ok) throw Error("Workspace unavailable");
    const body = await response.json();
    if (
      typeof body.name !== "string" ||
      !body.name ||
      body.name.length > 256 ||
      typeof body.role !== "string" ||
      !Object.hasOwn(ROLE_NAMES, body.role)
    )
      throw Error("Invalid workspace response");
    return {
      authenticated: true,
      workspace: {
        name: body.name,
        role: body.role as PublicWorkspace["role"],
      },
    };
  } catch {
    return { authenticated: true, workspace: null };
  }
}

export { verifyLogout } from "./backchannel";
