"use client";

import { useEffect, useRef, useState } from "react";
import {
  createSchedule,
  controlSchedule,
  readSchedules,
} from "../../app/workspace/schedule-actions";
import type { UniverseSnapshot } from "./universe-server";
import type { ScheduleRecord } from "./schedule-types";

const reasons = {
  authorization: "执行主体权限失效；恢复权限后重新启用。",
  source_unavailable: "来源权限、连接或更新策略不可用；修复后重新启用。",
  approval_required:
    "当前任务规模超出批准或硬限制；需停用并创建批准范围合适的新计划。",
};
const errors: Record<string, string> = {
  unavailable: "独立采集服务暂不可用，已有计划状态未能更新，请稍后刷新。",
  forbidden: "当前用户或原执行主体没有管理权限。",
  unauthorized: "来源授权不可用，请检查采集服务配置。",
  unsupported:
    "当前仅开放有已验证更新规则的 A 股日线；其他类别尚不支持自动更新。",
  invalid_input: "请检查标的范围、时间与批准上限，并明确确认自动执行。",
  conflict: "计划版本已变化，请刷新后操作。",
  unverified: "来源更新证据未通过检查，未启用计划。",
};

export function SchedulePanel({
  canManage,
  snapshots,
  names,
}: {
  canManage: boolean;
  snapshots: UniverseSnapshot[];
  names: Record<string, string>;
}) {
  const [records, setRecords] = useState<ScheduleRecord[]>([]);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [id, setId] = useState("");
  const [name, setName] = useState("日线自动补缺");
  const [symbols, setSymbols] = useState("");
  const [pool, setPool] = useState("");
  const [time, setTime] = useState("17:15");
  const [cadence, setCadence] = useState<"daily" | "weekly" | "quarterly">(
    "daily",
  );
  const [weekday, setWeekday] = useState(4);
  const [lookback, setLookback] = useState(366);
  const [catchup, setCatchup] = useState(7);
  const [parts, setParts] = useState(100);
  const [credits, setCredits] = useState(500);
  const [confirmed, setConfirmed] = useState(false);
  const [notice, setNotice] = useState("");
  const queryGeneration = useRef(0);

  async function refresh(page = offset) {
    const generation = ++queryGeneration.current;
    const result = await readSchedules(page);
    if (generation !== queryGeneration.current) return;
    if (result.records) {
      setRecords(result.records);
      setOffset(page);
    }
    setError(result.error ?? "");
  }
  useEffect(() => {
    let active = true;
    const generation = ++queryGeneration.current;
    void readSchedules(0).then((result) => {
      if (!active || generation !== queryGeneration.current) return;
      // Ignore a slower initial read after a mutation/refetch or workspace
      // unmount. The displayed list must describe the latest requested state.
      setId((current) => current || crypto.randomUUID());
      if (result.records) setRecords(result.records);
      setError(result.error ?? "");
    });
    return () => {
      active = false;
    };
  }, []);
  function changed() {
    setId(crypto.randomUUID());
    setConfirmed(false);
    setNotice("");
  }
  async function save() {
    setBusy(true);
    setError("");
    try {
      const result = await createSchedule({
        schedule_id: id,
        name,
        instrument_ids: [...new Set(symbols.split(/[\s,，]+/).filter(Boolean))],
        ...(pool ? { snapshot_id: pool } : {}),
        cadence,
        weekday,
        local_time: `${time}:00`,
        timezone: "Asia/Shanghai",
        lookback_days: lookback,
        catchup_days: catchup,
        approved_parts: parts,
        approved_credits: credits,
        automation_confirmed: confirmed,
      });
      if (result.error) {
        setError(result.error);
        return;
      }
      setNotice(
        "计划已保存为停用；核对冻结范围后点击启用。首次运行不会立即下载。",
      );
      setConfirmed(false);
      setId(crypto.randomUUID());
      await refresh(0);
    } finally {
      setBusy(false);
    }
  }
  async function control(record: ScheduleRecord, action: "enable" | "disable") {
    setBusy(true);
    setError("");
    try {
      const result = await controlSchedule(
        record.draft.schedule_id,
        record.revision,
        action,
      );
      if (result.error) setError(result.error);
      else await refresh();
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel schedule-panel" aria-label="自动更新计划">
      <div className="data-section-heading">
        <div>
          <span className="eyebrow">AUTOMATION</span>
          <h2>自动更新</h2>
          <p className="data-secondary">
            MaiRui A 股日线 · 仅补缺 ·
            北京时间。按交易日历和预计发布时间触发；休市正常跳过。
          </p>
        </div>
        <button
          className="button button-secondary"
          disabled={busy}
          onClick={() => void refresh()}
        >
          刷新自动计划
        </button>
      </div>
      {error && (
        <p role="alert">
          {errors[error] ?? "操作未完成，请检查连接和输入后重试。"}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {canManage && (
        <details className="schedule-create">
          <summary>创建自动更新计划</summary>
          <div className="schedule-form">
            <label>
              计划名称
              <input
                aria-label="自动计划名称"
                value={name}
                onChange={(e) => {
                  changed();
                  setName(e.target.value);
                }}
              />
            </label>
            <label>
              明确标的
              <input
                aria-label="自动计划标的"
                placeholder="601288.SH，多标的以逗号分隔"
                value={symbols}
                onChange={(e) => {
                  changed();
                  setSymbols(e.target.value);
                }}
              />
            </label>
            <label>
              或冻结股票池
              <select
                aria-label="自动计划股票池"
                value={pool}
                onChange={(e) => {
                  changed();
                  setPool(e.target.value);
                }}
              >
                <option value="">请选择；明确标的优先</option>
                {snapshots.map((s) => (
                  <option key={s.snapshot_id} value={s.snapshot_id}>
                    {s.name} · {s.members.length} 个成员 · v{s.version}
                  </option>
                ))}
              </select>
            </label>
            <label>
              运行节奏
              <select
                aria-label="自动计划节奏"
                value={cadence}
                onChange={(e) => {
                  changed();
                  setCadence(e.target.value as typeof cadence);
                }}
              >
                <option value="daily">每日</option>
                <option value="weekly">每周</option>
                <option value="quarterly">季度首日</option>
              </select>
            </label>
            {cadence === "weekly" && (
              <label>
                星期
                <select
                  aria-label="自动计划星期"
                  value={weekday}
                  onChange={(e) => {
                    changed();
                    setWeekday(Number(e.target.value));
                  }}
                >
                  {["一", "二", "三", "四", "五", "六", "日"].map((s, i) => (
                    <option key={s} value={i}>
                      周{s}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label>
              北京时间
              <input
                aria-label="自动计划运行时间"
                type="time"
                value={time}
                onChange={(e) => {
                  changed();
                  setTime(e.target.value);
                }}
              />
            </label>
            <label>
              回看天数
              <input
                aria-label="自动计划回看天数"
                type="number"
                min="1"
                max="3660"
                value={lookback}
                onChange={(e) => {
                  changed();
                  setLookback(Number(e.target.value));
                }}
              />
            </label>
            <label>
              停机补跑窗口（天）
              <input
                aria-label="自动计划补跑天数"
                type="number"
                min="1"
                max="31"
                value={catchup}
                onChange={(e) => {
                  changed();
                  setCatchup(Number(e.target.value));
                }}
              />
            </label>
            <label>
              每次批准新增分段
              <input
                aria-label="自动计划分段上限"
                type="number"
                min="1"
                max="1000000"
                value={parts}
                onChange={(e) => {
                  changed();
                  setParts(Number(e.target.value));
                }}
              />
            </label>
            <label>
              每次批准信用估计
              <input
                aria-label="自动计划信用上限"
                type="number"
                min="1"
                max="1000000000"
                value={credits}
                onChange={(e) => {
                  changed();
                  setCredits(Number(e.target.value));
                }}
              />
            </label>
          </div>
          <p className="data-footnote">
            日线为原始未复权口径。每次检查当前权限及批准上限；信用是无重试估计，实际调用仍受硬预算保护。停用不取消已入队任务或删除产物。修改冻结范围需创建新计划。
          </p>
          <label className="schedule-ack">
            <input
              type="checkbox"
              aria-label="确认自动计划授权"
              checked={confirmed}
              onChange={(e) => setConfirmed(e.target.checked)}
            />
            我批准在上述冻结范围与上限内自动补缺，并理解首次运行时间及停用语义。
          </label>
          <button
            className="button button-primary"
            disabled={busy || !id || !confirmed || (!symbols.trim() && !pool)}
            onClick={() => void save()}
          >
            保存停用计划
          </button>
        </details>
      )}
      <div
        className="schedule-list"
        role="region"
        aria-label="已保存更新计划"
        tabIndex={0}
      >
        {records.map((record) => (
          <article
            className="schedule-card"
            key={record.draft.schedule_id}
            data-schedule-id={record.draft.schedule_id}
          >
            <div className="data-section-heading">
              <h3>{record.draft.name}</h3>
              <span role="status">
                {
                  { enabled: "已启用", disabled: "已停用", paused: "已暂停" }[
                    record.state
                  ]
                }
              </span>
            </div>
            <p>
              {record.draft.selection.instrument_ids
                .slice(0, 5)
                .map((s) => `${names[s] ? `${names[s]} · ` : ""}${s}`)
                .join("、")}
              {record.draft.selection.instrument_ids.length > 5
                ? ` 等 ${record.draft.selection.instrument_ids.length} 个标的`
                : ""}
            </p>
            <p className="data-secondary">
              {record.draft.cadence === "daily"
                ? "每日"
                : record.draft.cadence === "weekly"
                  ? "每周"
                  : "季度首日"}{" "}
              {record.draft.local_time.slice(0, 5)} · {record.draft.timezone} ·
              回看 {record.draft.lookback_days} 天 · 批准{" "}
              {record.draft.approved_parts} 段 / {record.draft.approved_credits}{" "}
              信用估计
            </p>
            <p>
              下一运行槽：
              {new Intl.DateTimeFormat("zh-CN", {
                timeZone: record.draft.timezone,
                dateStyle: "short",
                timeStyle: "short",
              }).format(new Date(record.next_due_at))}
            </p>
            {record.pause_reason && (
              <p role="status">{reasons[record.pause_reason]}</p>
            )}
            {canManage && (
              <button
                className="button button-secondary"
                disabled={busy}
                onClick={() =>
                  void control(
                    record,
                    record.state === "enabled" ? "disable" : "enable",
                  )
                }
              >
                {record.state === "enabled" ? "停用自动计划" : "启用自动计划"}
              </button>
            )}
          </article>
        ))}
      </div>
      {!records.length && !error && (
        <p className="data-secondary">
          暂无自动计划。保存后默认停用，启用前再次检查当前授权和来源策略。
        </p>
      )}
      <div className="data-actions">
        <button
          className="button"
          disabled={busy || offset === 0}
          onClick={() => void refresh(Math.max(0, offset - 100))}
        >
          上一页计划
        </button>
        <span>第 {offset / 100 + 1} 页 · 每页最多100项</span>
        <button
          className="button"
          disabled={busy || records.length < 100}
          onClick={() => void refresh(offset + 100)}
        >
          下一页计划
        </button>
      </div>
    </section>
  );
}
