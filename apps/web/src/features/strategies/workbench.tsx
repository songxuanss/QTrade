"use client";

import dynamic from "next/dynamic";
import { loader } from "@monaco-editor/react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import type {
  StrategyLifecycleInfo,
  StrategyVersionInfo,
  WorkspaceStrategy,
} from "../auth/server";
import {
  saveStrategy,
  validateStrategyCode,
  validateStrategyParameters,
  markStrategyVersionValidated,
} from "../../app/workspace/strategy-actions";

loader.config({ paths: { vs: "/monaco/vs" } });

const Editor = dynamic(() => import("@monaco-editor/react"), {
  ssr: false,
  loading: () => <div className="strategy-editor-loading">正在加载编辑器…</div>,
});

const DEFAULT_CODE = `from qtrade import Strategy

class MovingAverage(Strategy):
    """SMA crossover: buy on a golden cross and sell on a death cross."""

    def on_start(self):
        self.fast = int(self.parameters.get("fast", 5))
        self.slow = int(self.parameters.get("slow", 20))
        self.quantity = int(self.parameters.get("quantity", 1000))
        if self.fast >= self.slow:
            raise ValueError("fast must be smaller than slow")
        self.closes = []
        self.holding = 0

    def on_bar(self, bar):
        self.closes.append(bar.close)
        if len(self.closes) < self.slow + 1:
            return
        fast_now = sum(self.closes[-self.fast:]) / self.fast
        slow_now = sum(self.closes[-self.slow:]) / self.slow
        fast_prev = sum(self.closes[-self.fast - 1:-1]) / self.fast
        slow_prev = sum(self.closes[-self.slow - 1:-1]) / self.slow
        if fast_prev <= slow_prev and fast_now > slow_now and self.holding == 0:
            self.buy(self.quantity)
            self.holding = self.quantity
        elif fast_prev >= slow_prev and fast_now < slow_now and self.holding > 0:
            self.sell(self.holding)
            self.holding = 0
`;
const DEFAULT_SCHEMA = `{
  "type": "object",
  "properties": {
    "fast": {"type": "integer", "default": 5, "minimum": 2, "maximum": 60},
    "slow": {"type": "integer", "default": 20, "minimum": 3, "maximum": 250},
    "quantity": {"type": "integer", "default": 1000, "minimum": 100, "maximum": 1000000}
  },
  "required": [],
  "additionalProperties": false
}`;

type ParameterRule = {
  type?: string;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  enum?: unknown[];
};

function parseSchema(source: string): Record<string, unknown> | null {
  try {
    const value = JSON.parse(source) as unknown;
    return value && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

function schemaProperties(schema: Record<string, unknown> | null) {
  const properties = schema?.properties;
  if (
    !properties ||
    typeof properties !== "object" ||
    Array.isArray(properties)
  )
    return [] as [string, ParameterRule][];
  return Object.entries(properties).filter(
    (entry): entry is [string, ParameterRule] =>
      Boolean(entry[1]) &&
      typeof entry[1] === "object" &&
      !Array.isArray(entry[1]),
  );
}

export function StrategyWorkbench({
  strategies,
  selected,
  versions,
  lifecycle,
  code: savedCode,
  dependencies: savedDependencies,
  canEdit,
  error,
  result,
}: {
  strategies: WorkspaceStrategy[];
  selected: WorkspaceStrategy | null;
  versions: StrategyVersionInfo[];
  lifecycle: StrategyLifecycleInfo | null;
  code: string;
  dependencies: string;
  canEdit: boolean;
  error: boolean;
  result?: string;
}) {
  const router = useRouter();
  const [name, setName] = useState(selected?.name ?? "");
  const [description, setDescription] = useState(selected?.description ?? "");
  const [code, setCode] = useState(savedCode || DEFAULT_CODE);
  const [schemaText, setSchemaText] = useState(
    versions.at(-1)?.parameter_schema
      ? JSON.stringify(versions.at(-1)!.parameter_schema, null, 2)
      : DEFAULT_SCHEMA,
  );
  const [dependenciesText, setDependenciesText] = useState(
    savedDependencies || "{}",
  );
  const [parameters, setParameters] = useState<Record<string, unknown>>({});
  const [notice, setNotice] = useState(
    result === "saved" ? "策略版本已保存" : "",
  );
  const [errorMessage, setErrorMessage] = useState(
    error ? "策略数据暂不可用，请稍后重试。" : "",
  );
  const [saving, setSaving] = useState(false);
  const [validating, setValidating] = useState(false);
  const [validated, setValidated] = useState(false);
  const [staticValidating, setStaticValidating] = useState(false);
  const [staticPassed, setStaticPassed] = useState<boolean | null>(null);
  const [staticDiagnostics, setStaticDiagnostics] = useState<
    { code: string; message: string; line: number | null }[]
  >([]);
  const schema = useMemo(() => parseSchema(schemaText), [schemaText]);
  const properties = useMemo(() => schemaProperties(schema), [schema]);
  const serverValidated = [
    "validated",
    "backtested",
    "paper",
    "approved",
    "live",
    "paused",
  ].includes(lifecycle?.state ?? "");
  const canBacktest = validated || serverValidated;
  const required = Array.isArray(schema?.required)
    ? (schema.required.filter(
        (value): value is string => typeof value === "string",
      ) as string[])
    : [];
  const currentVersion = selected
    ? (versions.find((item) => item.version === selected.current_version) ??
      versions.at(-1))
    : undefined;

  async function save() {
    setErrorMessage("");
    setNotice("");
    if (!canEdit) return;
    if (!schema) {
      setErrorMessage(
        "参数 schema 必须是有效 JSON 对象。请检查括号、引号和逗号。",
      );
      return;
    }
    let dependencies: Record<string, string>;
    try {
      const parsed = JSON.parse(dependenciesText) as unknown;
      if (
        !parsed ||
        typeof parsed !== "object" ||
        Array.isArray(parsed) ||
        Object.values(parsed).some((value) => typeof value !== "string")
      )
        throw Error("invalid dependencies");
      dependencies = parsed as Record<string, string>;
    } catch {
      setErrorMessage("依赖清单必须是“包名: 版本”的 JSON 对象。");
      return;
    }
    if (!selected && !name.trim()) {
      setErrorMessage("请填写策略名称。");
      return;
    }
    setSaving(true);
    const outcome = await saveStrategy({
      ...(selected
        ? { strategyId: selected.strategy_id }
        : { name: name.trim(), description: description.trim() }),
      code,
      parameterSchema: schema,
      dependencies,
    });
    setSaving(false);
    if (!outcome.ok || !outcome.strategyId) {
      setErrorMessage(outcome.error ?? "策略保存失败。");
      return;
    }
    setNotice(`v${outcome.version} 已保存，旧版本保持不变。`);
    setValidated(false);
    router.replace(`/workspace/strategies?strategy_id=${outcome.strategyId}`);
    router.refresh();
  }

  async function validate() {
    if (!selected || !currentVersion) {
      setErrorMessage("请先保存策略版本和参数 schema。");
      return;
    }
    if (lifecycle?.state === "draft" && staticPassed !== true) {
      setErrorMessage("请先完成静态校验，再校验参数。");
      return;
    }
    setErrorMessage("");
    setNotice("");
    setValidating(true);
    const outcome = await validateStrategyParameters(
      selected.strategy_id,
      currentVersion.version,
      parameters,
    );
    setValidating(false);
    if (!outcome.ok) {
      setValidated(false);
      setErrorMessage(outcome.error ?? "参数校验失败。");
      return;
    }
    setParameters(outcome.parameters ?? {});
    setValidated(true);
    const marked = await markStrategyVersionValidated(
      selected.strategy_id,
      currentVersion.version,
    );
    if (!marked.ok) {
      setValidated(false);
      setErrorMessage(marked.error ?? "版本状态更新失败。");
      return;
    }
    setNotice(
      "参数符合此 Strategy Version 的 schema，版本状态已更新为“已校验”。",
    );
    router.refresh();
  }

  async function validateSource() {
    setErrorMessage("");
    setNotice("");
    setStaticDiagnostics([]);
    let dependencies: Record<string, string>;
    try {
      const parsed = JSON.parse(dependenciesText) as unknown;
      if (
        !parsed ||
        typeof parsed !== "object" ||
        Array.isArray(parsed) ||
        Object.values(parsed).some((value) => typeof value !== "string")
      )
        throw Error("invalid dependencies");
      dependencies = parsed as Record<string, string>;
    } catch {
      setStaticPassed(false);
      setStaticDiagnostics([
        {
          code: "dependency.manifest",
          message: "依赖清单必须是有效的 JSON 对象。",
          line: null,
        },
      ]);
      return;
    }
    setStaticValidating(true);
    const outcome = await validateStrategyCode(code, dependencies);
    setStaticValidating(false);
    if (!outcome.ok) {
      setStaticPassed(false);
      setErrorMessage(outcome.error ?? "策略静态校验失败。 ");
      return;
    }
    setStaticPassed(outcome.passed ?? false);
    setStaticDiagnostics(
      (outcome.diagnostics ?? []).map(
        ({ code: diagnosticCode, message, line }) => ({
          code: diagnosticCode,
          message,
          line,
        }),
      ),
    );
    setNotice(
      outcome.passed
        ? `静态校验通过 · ${outcome.astNodeCount ?? 0} 个语法节点`
        : "静态校验发现需要修复的问题。",
    );
  }

  function updateParameter(name: string, value: unknown) {
    setValidated(false);
    setNotice("");
    setParameters((current) => ({ ...current, [name]: value }));
  }

  return (
    <div className="workspace-page strategy-workbench-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">RESEARCH / STRATEGY DESIGN</p>
          <h1>策略研究</h1>
          <p className="page-description">
            用不可变 Strategy Version 保存代码、参数约束与依赖环境。
          </p>
        </div>
        <Link
          className="button button-primary strategy-new-button"
          href="/workspace/strategies"
        >
          ＋ 新建策略
        </Link>
      </div>
      {errorMessage && (
        <p className="strategy-message error" role="alert">
          {errorMessage}
        </p>
      )}
      {notice && (
        <p
          className={`strategy-message ${validated || staticPassed ? "success" : staticPassed === false ? "error" : "info"}`}
          role="status"
        >
          {notice}
        </p>
      )}
      <div className="strategy-workspace-grid">
        <aside className="panel strategy-library" aria-label="策略库">
          <div className="strategy-library-heading">
            <div>
              <p className="eyebrow">LIBRARY</p>
              <h2>策略库</h2>
            </div>
            <span>{strategies.length}</span>
          </div>
          {strategies.length ? (
            <nav className="strategy-library-list">
              {strategies.map((strategy) => (
                <Link
                  key={strategy.strategy_id}
                  href={`/workspace/strategies?strategy_id=${strategy.strategy_id}`}
                  className={`strategy-library-item ${selected?.strategy_id === strategy.strategy_id ? "active" : ""}`}
                >
                  <span className="strategy-library-icon">∿</span>
                  <span>
                    <strong>{strategy.name}</strong>
                    <small>
                      v{strategy.current_version}
                      {strategy.archived ? " · 已归档" : ""}
                    </small>
                  </span>
                  <span aria-hidden="true">›</span>
                </Link>
              ))}
            </nav>
          ) : (
            <p className="strategy-library-empty">
              尚无策略。创建后，版本和代码会在此处保留。
            </p>
          )}
          {error && <p className="strategy-library-error">策略列表加载失败</p>}
        </aside>
        <section className="strategy-workspace-main">
          <section className="panel strategy-editor-panel">
            <div className="strategy-panel-heading">
              <div>
                <p className="eyebrow">
                  {selected
                    ? `STRATEGY / VERSION ${currentVersion?.version ?? selected.current_version}`
                    : "NEW STRATEGY"}
                </p>
                <h2>{selected?.name ?? "创建策略"}</h2>
              </div>
              {currentVersion && (
                <code>{currentVersion.code_sha256.slice(0, 12)}</code>
              )}
            </div>
            {!selected && (
              <div className="strategy-meta-form">
                <label>
                  策略名称
                  <input
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    maxLength={120}
                    disabled={!canEdit}
                    placeholder="例如：沪深 300 动量"
                  />
                </label>
                <label>
                  用途说明
                  <input
                    value={description}
                    onChange={(event) => setDescription(event.target.value)}
                    maxLength={2000}
                    disabled={!canEdit}
                    placeholder="简要说明策略假设"
                  />
                </label>
              </div>
            )}
            <div className="strategy-editor-caption">
              <span>Python 策略源码</span>
              <span>UTF-8 · 最大 1 MB</span>
            </div>
            <div className="strategy-monaco-shell">
              <Editor
                height="420px"
                language="python"
                theme="qtrade-dark"
                value={code}
                onChange={(value) => {
                  setCode(value ?? "");
                  setValidated(false);
                  setStaticPassed(null);
                  setStaticDiagnostics([]);
                }}
                beforeMount={(monaco) => {
                  monaco.editor.defineTheme("qtrade-dark", {
                    base: "vs-dark",
                    inherit: true,
                    rules: [],
                    colors: {
                      "editor.background": "#10151d",
                      "editor.foreground": "#d4dce5",
                      "editorLineNumber.foreground": "#8193a8",
                      "editorLineNumber.activeForeground": "#93a5b8",
                      "editorCursor.foreground": "#7dc7b0",
                      "editor.selectionBackground": "#285a5066",
                      "editor.lineHighlightBackground": "#17202a",
                    },
                  });
                }}
                options={{
                  readOnly: !canEdit || Boolean(selected?.archived),
                  minimap: { enabled: false },
                  fontSize: 12,
                  lineHeight: 21,
                  padding: { top: 14, bottom: 14 },
                  scrollBeyondLastLine: false,
                  automaticLayout: true,
                  tabSize: 4,
                  insertSpaces: true,
                  wordWrap: "on",
                  renderLineHighlight: "gutter",
                  scrollbar: {
                    verticalScrollbarSize: 8,
                    horizontalScrollbarSize: 8,
                  },
                }}
              />
            </div>
            {canEdit && !selected?.archived && (
              <div className="strategy-editor-footer">
                <span>保存会创建新的不可变版本，已保存版本不会被覆盖。</span>
                <div className="strategy-editor-actions">
                  <button
                    className="button button-secondary"
                    type="button"
                    onClick={validateSource}
                    disabled={staticValidating}
                  >
                    {staticValidating ? "正在校验…" : "静态校验"}
                  </button>
                  <button
                    className="button button-primary"
                    type="button"
                    onClick={save}
                    disabled={saving}
                  >
                    {saving
                      ? "正在保存…"
                      : selected
                        ? "保存为新版本"
                        : "创建策略"}
                  </button>
                </div>
              </div>
            )}
            {staticDiagnostics.length > 0 && (
              <ul
                className="strategy-static-diagnostics"
                aria-label="静态校验结果"
                role="list"
              >
                {staticDiagnostics.map((diagnostic, index) => (
                  <li key={`${diagnostic.code}-${diagnostic.line}-${index}`}>
                    <code>{diagnostic.code}</code>
                    <span>{diagnostic.message}</span>
                    {diagnostic.line !== null && (
                      <small>第 {diagnostic.line} 行</small>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <div className="strategy-support-grid">
            <section className="panel strategy-schema-panel">
              <div className="strategy-subheading">
                <div>
                  <p className="eyebrow">PARAMETERS</p>
                  <h2>参数 schema</h2>
                </div>
                <span>JSON</span>
              </div>
              <textarea
                aria-label="参数 schema JSON"
                className="strategy-json-editor"
                value={schemaText}
                onChange={(event) => {
                  setSchemaText(event.target.value);
                  setValidated(false);
                }}
                readOnly={!canEdit || Boolean(selected?.archived)}
                spellCheck={false}
              />
              <div className="strategy-subheading strategy-parameters-heading">
                <div>
                  <p className="eyebrow">INPUT VALUES</p>
                  <h2>参数表单</h2>
                </div>
                <span>{properties.length} 项</span>
              </div>
              {properties.length ? (
                <div className="strategy-parameter-fields">
                  {properties.map(([field, rule]) => (
                    <label key={field}>
                      <span>
                        {field}
                        {required.includes(field) && <b>必填</b>}
                      </span>
                      {rule.enum ? (
                        <select
                          value={String(
                            parameters[field] ??
                              rule.default ??
                              rule.enum[0] ??
                              "",
                          )}
                          onChange={(event) => {
                            const selected = rule.enum?.find(
                              (item) => String(item) === event.target.value,
                            );
                            updateParameter(
                              field,
                              selected ?? event.target.value,
                            );
                          }}
                        >
                          {rule.enum.map((item) => (
                            <option key={String(item)} value={String(item)}>
                              {String(item)}
                            </option>
                          ))}
                        </select>
                      ) : rule.type === "boolean" ? (
                        <input
                          type="checkbox"
                          checked={
                            typeof parameters[field] === "boolean"
                              ? Boolean(parameters[field])
                              : Boolean(rule.default ?? false)
                          }
                          onChange={(event) =>
                            updateParameter(field, event.target.checked)
                          }
                        />
                      ) : (
                        <input
                          type={
                            rule.type === "integer" || rule.type === "number"
                              ? "number"
                              : "text"
                          }
                          step={
                            rule.type === "integer"
                              ? 1
                              : rule.type === "number"
                                ? "any"
                                : undefined
                          }
                          min={rule.minimum}
                          max={rule.maximum}
                          minLength={rule.minLength}
                          maxLength={rule.maxLength}
                          value={String(
                            parameters[field] ?? rule.default ?? "",
                          )}
                          onChange={(event) =>
                            updateParameter(
                              field,
                              rule.type === "integer" || rule.type === "number"
                                ? event.target.value === ""
                                  ? ""
                                  : Number(event.target.value)
                                : event.target.value,
                            )
                          }
                        />
                      )}
                    </label>
                  ))}
                </div>
              ) : (
                <p className="strategy-parameters-empty">
                  保存包含 properties 的 schema 后，参数表单会在此生成。
                </p>
              )}
              <div className="strategy-validate-row">
                <span>默认值和范围由当前版本锁定。</span>
                <button
                  className="button button-secondary"
                  type="button"
                  onClick={validate}
                  disabled={
                    !selected || !canEdit || validating || !currentVersion
                  }
                >
                  {validating ? "校验中…" : "校验参数"}
                </button>
              </div>
            </section>
            <section className="panel strategy-dependencies-panel">
              <div className="strategy-subheading">
                <div>
                  <p className="eyebrow">ENVIRONMENT</p>
                  <h2>依赖清单</h2>
                </div>
                <span>PINNED</span>
              </div>
              <textarea
                aria-label="策略依赖清单 JSON"
                className="strategy-json-editor dependencies"
                value={dependenciesText}
                onChange={(event) => {
                  setDependenciesText(event.target.value);
                  setValidated(false);
                  setStaticPassed(null);
                  setStaticDiagnostics([]);
                }}
                readOnly={!canEdit || Boolean(selected?.archived)}
                spellCheck={false}
                placeholder={'{\n  "numpy": "==2.1.0"\n}'}
              />
              <p className="strategy-dependencies-help">
                依赖清单随版本保存并参与 SHA-256 校验。
              </p>
              <div className="strategy-subheading strategy-versions-heading">
                <div>
                  <p className="eyebrow">IMMUTABLE HISTORY</p>
                  <h2>版本记录</h2>
                </div>
                <span>{versions.length}</span>
              </div>
              {selected && lifecycle && (
                <section
                  className="strategy-lifecycle-card"
                  aria-label="版本生命周期"
                >
                  <div>
                    <span
                      className={`strategy-state-chip state-${lifecycle.state}`}
                    >
                      {lifecycle.state === "draft"
                        ? "草稿"
                        : lifecycle.state === "validated"
                          ? "已校验"
                          : lifecycle.state === "backtested"
                            ? "回测通过"
                            : lifecycle.state === "paper"
                              ? "模拟观察"
                              : lifecycle.state === "approved"
                                ? "已审批"
                                : lifecycle.state === "live"
                                  ? "实盘中"
                                  : lifecycle.state === "paused"
                                    ? "已暂停"
                                    : lifecycle.state === "failed"
                                      ? "失败"
                                      : "已归档"}
                    </span>
                    <small>
                      v{currentVersion?.version} · 状态更新时间{" "}
                      {new Date(lifecycle.updated_at).toLocaleString("zh-CN")}
                    </small>
                  </div>
                  {lifecycle.state === "approved" && (
                    <p>
                      审批已绑定此不可变版本；执行运行时接入后才能启动实盘。
                    </p>
                  )}
                  {lifecycle.approval && (
                    <p>
                      风险审批 · 模拟观察{" "}
                      {lifecycle.approval.paper_observations} 次
                    </p>
                  )}
                  {lifecycle.history.length > 0 && (
                    <ol aria-label="状态转换历史">
                      {lifecycle.history.map((event) => (
                        <li key={event.transition_id}>
                          <span>
                            {event.from_state} → {event.to_state}
                          </span>
                          <time dateTime={event.occurred_at}>
                            {new Date(event.occurred_at).toLocaleString(
                              "zh-CN",
                            )}
                          </time>
                        </li>
                      ))}
                    </ol>
                  )}
                </section>
              )}
              {versions.length ? (
                <ol className="strategy-version-list">
                  {versions
                    .slice()
                    .reverse()
                    .map((version) => (
                      <li key={version.strategy_version_id}>
                        <span className="strategy-version-mark">
                          v{version.version}
                        </span>
                        <span>
                          <strong>
                            {version.parameter_schema.properties &&
                            typeof version.parameter_schema.properties ===
                              "object"
                              ? Object.keys(version.parameter_schema.properties)
                                  .length
                              : 0}{" "}
                            个参数
                          </strong>
                          <small>
                            {version.code_sha256.slice(0, 12)} ·{" "}
                            {version.dependencies_sha256.slice(0, 8)}
                          </small>
                        </span>
                        <span>
                          {version.version === selected?.current_version
                            ? "当前"
                            : "已冻结"}
                        </span>
                      </li>
                    ))}
                </ol>
              ) : (
                <p className="strategy-parameters-empty">
                  首次保存后开始记录版本。
                </p>
              )}
            </section>
          </div>
          {selected && (
            <div className="strategy-run-gate">
              <span>
                {canBacktest
                  ? "参数通过，可前往回测实验选择数据和成本模型。"
                  : "先校验当前参数，再配置回测数据和成本模型。"}
              </span>
              {canBacktest ? (
                <Link
                  className="button button-primary"
                  href="/workspace/backtests"
                >
                  配置回测
                </Link>
              ) : (
                <button
                  type="button"
                  className="button button-primary"
                  disabled
                >
                  配置回测
                </button>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
