"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { IsoDateInput } from "../../components/ui/iso-date";
import {
  controlCampaign,
  previewCampaign,
  readCampaign,
  readCampaigns,
  submitCampaign,
} from "../../app/workspace/campaign-actions";
import type { CampaignPlan, CampaignRun } from "./campaign-types";
import type { UniverseSnapshot } from "./universe-server";

const labels: Record<string, string> = {
  queued: "等待执行",
  running: "执行中",
  partial: "部分完成",
  completed: "已完成",
  failed: "失败",
  cancelled: "已停止展开",
  viewable: "可查看",
  no_observation: "无观测 · 待核验",
  collected: "已采集 · 待发布",
  waiting: "等待来源额度",
  pending: "待展开",
};
const localDay = (value: string) =>
  new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    dateStyle: "medium",
  }).format(new Date(value));
const errors: Record<string, string> = {
  forbidden: "当前账号没有下载管理权限。",
  conflict: "覆盖或计划已变化，请重新预览。",
  unsupported: "该周期或标的暂不支持，请调整选择。",
  invalid_input: "日期或成员选择无效，请检查。",
  unavailable: "来源或服务暂不可用，请稍后重试。",
};
function dateDaysAgo(days: number) {
  const now = new Date(Date.now() + 8 * 3600000);
  now.setUTCDate(now.getUTCDate() - days);
  return now.toISOString().slice(0, 10);
}
function rangeDate(day: string, inclusiveEnd = false) {
  const value = new Date(`${day}T00:00:00+08:00`);
  if (inclusiveEnd) value.setUTCDate(value.getUTCDate() + 1);
  return value.toISOString();
}
export function CampaignPanel({
  snapshot,
  recent,
  canManage,
  periods,
}: {
  snapshot: UniverseSnapshot | null;
  recent: UniverseSnapshot[];
  canManage: boolean;
  periods: string[];
}) {
  const choices = [...(snapshot ? [snapshot] : []), ...recent].filter(
    (item, index, items) =>
      items.findIndex((other) => other.universe_id === item.universe_id) ===
      index,
  );
  const [selected, setSelected] = useState<string[]>(
    snapshot ? [snapshot.snapshot_id] : [],
  );
  const [start, setStart] = useState(dateDaysAgo(365));
  const [end, setEnd] = useState(dateDaysAgo(1));
  const [period, setPeriod] = useState("1d");
  const [mode, setMode] = useState<"missing" | "all">("missing");
  const [plan, setPlan] = useState<CampaignPlan | null>(null);
  const [run, setRun] = useState<CampaignRun | null>(null);
  const [runs, setRuns] = useState<CampaignRun[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [memberPage, setMemberPage] = useState(0);
  useEffect(() => {
    let current = true;
    void readCampaigns().then((items) => {
      if (current) {
        setRuns(items);
        const id = new URL(location.href).searchParams.get("campaign");
        if (id)
          void readCampaign(id).then((result) => {
            if (current) setRun(result.run ?? null);
          });
      }
    });
    return () => {
      current = false;
    };
  }, []);
  const id = run?.campaign_id;
  useEffect(() => {
    if (!id) return;
    let current = true;
    const refresh = async () => {
      const result = await readCampaign(id, memberPage * 30);
      if (current) {
        if (result.run) setRun(result.run);
        if (result.error) setError(result.error);
      }
    };
    const timer = setInterval(() => {
      void refresh();
    }, 5000);
    return () => {
      current = false;
      clearInterval(timer);
    };
  }, [id, memberPage]);
  const invalidate = () => {
    setPlan(null);
    setError("");
  };
  async function preview() {
    setBusy(true);
    setError("");
    setPlan(null);
    try {
      const result = await previewCampaign({
        snapshot_ids: selected,
        request_id: crypto.randomUUID(),
        granularity: period,
        start: rangeDate(start),
        end: rangeDate(end, true),
        mode,
      });
      if (result.plan) setPlan(result.plan);
      else setError(result.error ?? "unavailable");
    } catch {
      setError("invalid_input");
    } finally {
      setBusy(false);
    }
  }
  async function submit() {
    if (!plan) return;
    setBusy(true);
    setError("");
    try {
      const result = await submitCampaign(plan.plan_id, plan.coverage_version);
      if (result.campaign_id) {
        const loaded = await readCampaign(result.campaign_id);
        setRun(loaded.run ?? null);
        setMemberPage(0);
        const url = new URL(location.href);
        url.searchParams.set("campaign", result.campaign_id);
        history.replaceState(null, "", url);
        setRuns(await readCampaigns());
      } else setError(result.error ?? "unavailable");
    } finally {
      setBusy(false);
    }
  }
  async function control(action: "retry" | "cancel") {
    if (!run) return;
    setBusy(true);
    const result = await controlCampaign(run.campaign_id, action);
    if (result.error) setError(result.error);
    else setRun((await readCampaign(run.campaign_id)).run ?? run);
    setBusy(false);
  }
  const reused =
    plan?.members.reduce((sum, m) => sum + m.reused_task_ids.length, 0) ?? 0;
  const unique = new Set(
    choices
      .filter((item) => selected.includes(item.snapshot_id))
      .flatMap((item) =>
        item.members.map((member) => member.instrument.instrument_id),
      ),
  ).size;
  return (
    <section
      className="campaign-panel"
      id="pool-download"
      aria-labelledby="pool-download-title"
    >
      <div className="universe-heading">
        <div>
          <p className="eyebrow">BATCH COLLECTION</p>
          <h3 id="pool-download-title">板块一键下载</h3>
          <p className="page-description">
            按冻结股票池去重后批量创建任务。此流程与上方单股采集的周期设置相互独立。
          </p>
        </div>
        <div className="campaign-defaults" aria-label="批量下载默认设置">
          <span>默认设置</span>
          <strong>日线</strong>
          <strong>近一年</strong>
          <strong>仅补缺</strong>
        </div>
      </div>
      {canManage && periods.length === 0 && (
        <p className="data-notice" role="alert">
          下载来源能力暂不可用，请刷新页面后重试。
        </p>
      )}
      {error && (
        <p className="data-notice" role="alert">
          {errors[error] ?? errors.unavailable}
        </p>
      )}
      {canManage && choices.length > 0 && (
        <>
          <fieldset className="campaign-pools">
            <legend>选择股票池 · 所有成员参与</legend>
            <div className="campaign-tools">
              <button
                type="button"
                className="button button-secondary"
                onClick={() => {
                  setSelected(choices.map((item) => item.snapshot_id));
                  invalidate();
                }}
              >
                全选以下股票池
              </button>
              <button
                type="button"
                className="button button-secondary"
                onClick={() => {
                  setSelected(snapshot ? [snapshot.snapshot_id] : []);
                  invalidate();
                }}
              >
                仅当前板块
              </button>
              <span>
                已选 {selected.length} 个股票池 · 去重后 {unique} 只股票
              </span>
            </div>
            {choices.map((item) => (
              <label key={item.snapshot_id}>
                <input
                  type="checkbox"
                  checked={selected.includes(item.snapshot_id)}
                  onChange={(event) => {
                    setSelected((current) =>
                      event.target.checked
                        ? [...current, item.snapshot_id]
                        : current.filter((id) => id !== item.snapshot_id),
                    );
                    invalidate();
                  }}
                />
                {item.name} · v{item.version} · {item.members.length} 个成员
              </label>
            ))}
          </fieldset>
          <div className="campaign-config">
            <label>
              板块下载周期
              <select
                aria-label="板块下载周期"
                value={period}
                onChange={(e) => {
                  setPeriod(e.target.value);
                  invalidate();
                }}
              >
                {["1d", "1h", "15m", "5m", "1m", "tick"].map((value) => (
                  <option
                    key={value}
                    value={value}
                    disabled={!periods.includes(value)}
                  >
                    {value === "1d"
                      ? "日线"
                      : value === "tick"
                        ? "逐笔原始数据"
                        : value}
                    {!periods.includes(value) ? " · 未授权" : ""}
                  </option>
                ))}
              </select>
            </label>
            <IsoDateInput
              label="板块下载开始日期"
              value={start}
              onChange={(value) => {
                setStart(value);
                invalidate();
              }}
            />
            <IsoDateInput
              label="板块下载结束日期"
              value={end}
              onChange={(value) => {
                setEnd(value);
                invalidate();
              }}
            />
            <label>
              下载方式
              <select
                aria-label="板块下载方式"
                value={mode}
                onChange={(e) => {
                  setMode(e.target.value as "missing" | "all");
                  invalidate();
                }}
              >
                <option value="missing">仅下载缺失数据</option>
                <option value="all">重新下载所有数据</option>
              </select>
            </label>
          </div>
          <button
            className="button"
            disabled={
              busy || selected.length === 0 || !periods.includes(period)
            }
            onClick={() => {
              void preview();
            }}
          >
            {busy ? "正在处理…" : "预览全部成员下载任务"}
          </button>
          <p className="page-description">
            当前成员不代表历史成分；逐笔归档不等于分钟 K
            线。周期、时间范围和下载方式可在预览前调整。
          </p>
          {plan && (
            <div className="campaign-preview" aria-label="板块下载计划">
              <h4>下载计划 · {plan.members.length} 只股票</h4>
              <p>
                新建 {plan.planned_parts - reused} 个采集分段 · 复用或检查{" "}
                {reused} 个任务
              </p>
              {plan.planned_parts === reused && (
                <p role="status">
                  所选范围没有新增缺失分段，将检查已完成或进行中的任务。
                </p>
              )}
              <button
                className="button"
                disabled={busy}
                onClick={() => {
                  void submit();
                }}
              >
                一键下载所有成员
              </button>
            </div>
          )}
        </>
      )}
      {runs.length > 0 && (
        <details className="universe-recent">
          <summary>最近批量下载任务（{runs.length}）</summary>
          <ul>
            {runs.map((item) => (
              <li key={item.campaign_id}>
                <button
                  type="button"
                  className="campaign-run-link"
                  onClick={() => {
                    void readCampaign(item.campaign_id).then((result) => {
                      if (result.run) setRun(result.run);
                    });
                    setMemberPage(0);
                    const url = new URL(location.href);
                    url.searchParams.set("campaign", item.campaign_id);
                    history.replaceState(null, "", url);
                  }}
                >
                  {item.member_count} 只 · {item.granularity} ·{" "}
                  {item.start.slice(0, 10)} — {item.end.slice(0, 10)} ·{" "}
                  {labels[item.state] ?? item.state}
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
      {run && (
        <div className="campaign-progress" data-campaign-id={run.campaign_id}>
          <div className="universe-heading">
            <h4>整批进度 · {labels[run.state] ?? run.state}</h4>
            <span>
              {run.member_count} 只股票 · {run.granularity}
            </span>
          </div>
          <p className="page-description">
            本批次范围：{localDay(run.start)} —{" "}
            {localDay(new Date(Date.parse(run.end) - 1).toISOString())}
            （北京时间） · {run.mode === "missing" ? "仅补缺" : "重下载全部"}
          </p>
          <progress
            aria-label="板块采集进度"
            max={Math.max(1, run.planned_parts)}
            value={
              run.collected_parts +
              run.failed_parts +
              (run.no_observation_parts ?? 0)
            }
          />
          <div className="campaign-metrics">
            <span>
              已展开 {run.expanded_parts}/{run.planned_parts}
            </span>
            <span>
              已采集 {run.collected_parts}/{run.planned_parts}
            </span>
            <span>
              可查看 {run.viewable_members}/{run.member_count}
            </span>
            <span>失败 {run.failed_parts}</span>
            <span>无观测 {run.no_observation_members}</span>
          </div>
          {canManage && (
            <div className="campaign-tools">
              {run.failed_parts > 0 && (
                <button
                  className="button button-secondary"
                  disabled={busy}
                  onClick={() => {
                    void control("retry");
                  }}
                >
                  仅重试失败任务
                </button>
              )}
              {run.expanded_parts < run.planned_parts &&
                run.state !== "cancelled" && (
                  <button
                    className="button button-secondary"
                    disabled={busy}
                    onClick={() => {
                      void control("cancel");
                    }}
                  >
                    停止未展开任务
                  </button>
                )}
              <span>停止展开会保留已创建任务，已创建任务继续执行。</span>
            </div>
          )}
          <div className="campaign-members">
            {run.members.map((member) => (
              <details key={member.instrument_id} className="campaign-member">
                <summary>
                  <span>
                    {member.name}
                    <small>{member.instrument_id}</small>
                  </span>
                  <span>
                    {labels[member.state] ?? member.state} ·{" "}
                    {member.expanded_parts}/{member.planned_parts}
                  </span>
                </summary>
                {member.viewable && (
                  <Link
                    href={`/workspace/market?instrument=${member.instrument_id}&granularity=${run.granularity}`}
                  >
                    查看 {run.granularity} 行情
                  </Link>
                )}
                {member.parts.map((part) => (
                  <p key={part.task_id}>
                    {part.start.slice(0, 10)} — {part.end.slice(0, 10)} ·{" "}
                    {labels[part.state] ?? part.state}
                    {part.raw_records !== undefined
                      ? ` · ${part.raw_records} 条原始记录`
                      : ""}
                    {part.error_code ? ` · ${part.error_code}` : ""}
                  </p>
                ))}
                {member.parts_truncated && <p>这里只展示前 100 个分段。</p>}
              </details>
            ))}
          </div>
          <nav className="universe-pagination" aria-label="下载成员分页">
            <span>第 {memberPage + 1} 页</span>
            {memberPage > 0 && (
              <button
                type="button"
                onClick={() => {
                  const page = memberPage - 1;
                  setMemberPage(page);
                  void readCampaign(run.campaign_id, page * 30).then(
                    (result) => {
                      if (result.run) setRun(result.run);
                    },
                  );
                }}
              >
                上一页
              </button>
            )}
            {(memberPage + 1) * 30 < run.member_count && (
              <button
                type="button"
                onClick={() => {
                  const page = memberPage + 1;
                  setMemberPage(page);
                  void readCampaign(run.campaign_id, page * 30).then(
                    (result) => {
                      if (result.run) setRun(result.run);
                    },
                  );
                }}
              >
                下一页
              </button>
            )}
          </nav>
        </div>
      )}
      {!snapshot && choices.length === 0 && (
        <p>先选择板块并保存成员快照，即可一键下载全部成员。</p>
      )}
    </section>
  );
}
