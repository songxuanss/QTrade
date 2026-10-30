"use client";
import { useEffect, useId, useRef, useState } from "react";
import { readSourceProfiles } from "../../app/workspace/source-profile-actions";
import styles from "./source-profile-panel.module.css";

export type SourceProfileCatalog = {
  scope_id: string;
  profiles: {
    source: string;
    name: string;
    kind: string;
    mapping_version: string | null;
    markets: string[];
    periods: string[];
    instrument_kinds: string[];
    timezone: string;
    state: "configured" | "unconfigured" | "not_connected";
    facts: {
      aspect: string;
      detail: string;
      evidence: string;
      reference: string;
      as_of: string;
      unblock_condition: string | null;
    }[];
  }[];
};
const aspects: Record<string, string> = {
  history: "历史范围",
  units: "字段与单位",
  adjustment: "调整与版本",
  time: "时间语义",
  updates: "更新时间",
  permission: "权限证据",
  quota: "配额证据",
  license: "用途许可",
};
const evidence: Record<string, string> = {
  unknown: "待确认",
  documented: "规则说明",
  sample_verified: "样本已验证",
  e2e_verified: "指定场景端到端已验证",
};
const references: Record<string, string> = {
  "docs/data/CAPABILITY-AUDIT-2026-10-12.md": "现有授权下的有界探查",
  "docs/data/COLLECTION-ACCEPTANCE-SCOPE.md": "本轮固定接入范围",
  "docs/testing/COLLECTION-NETWORK-AND-FROZEN-CHART-2026-10-13.md":
    "真实采集、终端及刷新验证",
};

/** Dated capability evidence is not a live permission/health assertion.
 * Keep it folded and fetch on demand, so an unavailable catalog cannot hide
 * the existing collection form. Workspace changes remount this component. */
export function SourceProfilePanel() {
  const selectId = useId();
  const [catalog, setCatalog] = useState<SourceProfileCatalog | null>(null);
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const [missing, setMissing] = useState<string[]>([]);
  const generation = useRef(0);
  useEffect(
    () => () => {
      generation.current += 1;
    },
    [],
  );
  async function load() {
    const request = ++generation.current;
    setBusy(true);
    try {
      const result = await readSourceProfiles();
      if (request !== generation.current) return;
      setError(!result.catalog);
      if (result.catalog) {
        setCatalog(result.catalog);
        setMissing(result.missing ?? []);
        setSelected((current) =>
          result.catalog!.profiles.some((p) => p.source === current)
            ? current
            : (result.catalog!.profiles[0]?.source ?? ""),
        );
      }
    } catch {
      if (request === generation.current) setError(true);
    } finally {
      if (request === generation.current) setBusy(false);
    }
  }
  const profile = catalog?.profiles.find((p) => p.source === selected);
  return (
    <details
      className={styles.panel}
      data-source-profiles
      onToggle={(event) => {
        if (event.currentTarget.open && !catalog && !busy) void load();
      }}
    >
      <summary>
        <h2 className={styles.title}>来源能力与证据</h2>
      </summary>
      <div className={styles.header}>
        <p>
          先了解历史深度、字段口径与研究限制。已配置不代表当前授权或完整数据覆盖。
        </p>
        <button
          className="button button-secondary"
          disabled={busy}
          onClick={() => void load()}
        >
          刷新来源说明
        </button>
      </div>
      {busy && <p>正在读取来源说明…</p>}
      {error && (
        <p role="alert">来源说明暂不可用，保留的旧说明仅供参考；请重试。</p>
      )}
      {!error && missing.length > 0 && (
        <p role="alert">
          来源说明尚不完整，未能读取：{missing.join("、")}
          。当前仅展示已读取部分，请刷新重试。
        </p>
      )}
      {catalog && (
        <>
          <p role="status">
            {error
              ? "来源说明待重新核对"
              : `已读取 ${catalog.profiles.length} 项来源能力说明`}
          </p>
          <label htmlFor={selectId}>查看数据能力</label>
          <select
            id={selectId}
            value={selected}
            onChange={(event) => setSelected(event.target.value)}
          >
            {catalog.profiles.map((p) => (
              <option key={p.source} value={p.source}>
                {p.name}
              </option>
            ))}
          </select>
          {profile && (
            <section aria-label="所选来源证据">
              <h3>{profile.name}</h3>
              <p>
                {profile.state === "configured"
                  ? "适配器已配置，实际权限以请求校验为准"
                  : profile.state === "unconfigured"
                    ? "未配置，采集入口不可用"
                    : "尚未接通，不能创建采集任务"}
              </p>
              <p className={styles.hint}>
                {profile.source} · {profile.markets.join(" / ")} ·{" "}
                {profile.timezone}
                {profile.periods.length
                  ? ` · ${profile.periods.join(" / ")}`
                  : profile.kind === "current_membership"
                    ? " · 当前快照，不支持历史日期查询"
                    : profile.state === "not_connected"
                      ? " · 查询规则待接入"
                      : " · 按类别日期查询"}
                {profile.mapping_version
                  ? ` · ${profile.mapping_version}`
                  : " · 无已接入映射"}
              </p>
              <div
                className={styles.viewport}
                role="region"
                aria-label="来源证据明细"
                tabIndex={0}
              >
                <dl className={styles.facts}>
                  {profile.facts.map((f) => (
                    <div key={f.aspect}>
                      <dt>
                        {aspects[f.aspect] ?? f.aspect}{" "}
                        <span>{evidence[f.evidence] ?? "待确认"}</span>
                      </dt>
                      <dd>{f.detail}</dd>
                      {f.unblock_condition && (
                        <dd className={styles.hint}>
                          确认条件：{f.unblock_condition}
                        </dd>
                      )}
                      <dd className={styles.hint}>
                        核对截至 {f.as_of} · 证据：
                        {references[f.reference] ?? "来源能力核验记录"}
                      </dd>
                    </div>
                  ))}
                </dl>
              </div>
            </section>
          )}
        </>
      )}
    </details>
  );
}
