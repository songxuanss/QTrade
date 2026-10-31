"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import type { MembershipInfo } from "../auth/server";
import { addMembership } from "../../app/workspace/backtest-actions";

function toLocalInput(value: string) {
  const date = new Date(value);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

export function MembershipPanel({
  memberships,
  canManage,
}: {
  memberships: MembershipInfo[];
  canManage: boolean;
}) {
  const router = useRouter();
  const today = new Date();
  const [instrumentId, setInstrumentId] = useState("600519.SH");
  const [effectiveFrom, setEffectiveFrom] = useState(
    toLocalInput(new Date(today.getFullYear(), 0, 1).toISOString()),
  );
  const [effectiveTo, setEffectiveTo] = useState(
    toLocalInput(new Date(today.getFullYear() + 1, 0, 1).toISOString()),
  );
  const [evidence, setEvidence] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setMessage("");
    if (new Date(effectiveFrom) >= new Date(effectiveTo)) {
      setError("生效起必须早于生效止。");
      return;
    }
    setBusy(true);
    const result = await addMembership({
      instrumentId: instrumentId.trim(),
      effectiveFrom,
      effectiveTo,
      evidence: evidence.trim(),
    });
    setBusy(false);
    if (!result.ok) {
      setError(result.error ?? "成员区间保存失败。");
      return;
    }
    setMessage("成员区间已保存，可从下一次回测开始冻结。");
    setEvidence("");
    router.refresh();
  }

  return (
    <section
      className="panel membership-panel"
      aria-labelledby="membership-title"
    >
      <div className="panel-heading">
        <div>
          <p className="eyebrow">UNIVERSE MEMBERSHIP</p>
          <h2 id="membership-title">成员区间（研究定义）</h2>
        </div>
        <span className="backtest-count">{memberships.length}</span>
      </div>
      <p className="page-description">
        有效区间是半开区间 [生效起, 生效止)。启用成员校验的回测只在区间内交易，
        区间结束前按最后一个可用收盘价强制平仓。这里的区间由工作区填写并记录证据，
        不等同于交易所历史成分；如无官方区间，报告会明确披露幸存者偏差风险。
      </p>
      {canManage ? (
        <form className="backtest-form membership-form" onSubmit={submit}>
          <label>
            <span>标的</span>
            <input
              value={instrumentId}
              onChange={(event) => setInstrumentId(event.target.value)}
              placeholder="600519.SH"
              required
            />
          </label>
          <label>
            <span>生效起</span>
            <input
              type="datetime-local"
              value={effectiveFrom}
              onChange={(event) => setEffectiveFrom(event.target.value)}
              required
            />
          </label>
          <label>
            <span>生效止</span>
            <input
              type="datetime-local"
              value={effectiveTo}
              onChange={(event) => setEffectiveTo(event.target.value)}
              required
            />
          </label>
          <label>
            <span>证据说明</span>
            <input
              value={evidence}
              onChange={(event) => setEvidence(event.target.value)}
              placeholder="例如：研究口径定义的样本区间"
              required
            />
          </label>
          <button className="button button-secondary" disabled={busy}>
            {busy ? "保存中…" : "保存区间"}
          </button>
        </form>
      ) : (
        <p className="backtest-empty">当前角色只能查看成员区间。</p>
      )}
      {error && (
        <p className="strategy-message error" role="alert">
          {error}
        </p>
      )}
      {message && (
        <p className="strategy-message success" role="status">
          {message}
        </p>
      )}
      {memberships.length === 0 ? (
        <p className="backtest-empty">还没有成员区间。</p>
      ) : (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>标的</th>
                <th>生效起</th>
                <th>生效止</th>
                <th>来源</th>
                <th>证据</th>
                <th>区间版本</th>
              </tr>
            </thead>
            <tbody>
              {memberships.map((item) => (
                <tr key={item.membership_id}>
                  <td>{item.instrument_id}</td>
                  <td>{item.effective_from.slice(0, 10)}</td>
                  <td>{item.effective_to.slice(0, 10)}</td>
                  <td>
                    {item.source === "supplier.membership"
                      ? "供应方"
                      : "工作区定义"}
                  </td>
                  <td>{item.evidence}</td>
                  <td className="backtest-hash">
                    {item.content_version.slice(0, 12)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
