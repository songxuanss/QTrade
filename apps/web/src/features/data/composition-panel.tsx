import type { UniverseSnapshot } from "./universe-server";
import { composePoolDatasetAction } from "../../app/workspace/data-actions";

const notices: Record<string, string> = {
  composition_saved:
    "选股数据集已合成。它只包含股票池中覆盖完整的成员，可在回测页直接选择。",
  composition_rejected:
    "无法合成选股数据集：股票池成员在所选区间内覆盖不完整，或区间超出运行器预算。",
  composition_unavailable: "选股数据集服务暂不可用，请稍后重试。",
};

export function CompositionPanel({
  snapshots,
  canManage,
  result,
  composedDataset,
}: {
  snapshots: UniverseSnapshot[];
  canManage: boolean;
  result?: string;
  composedDataset?: string;
}) {
  const notice = result ? notices[result] : undefined;
  return (
    <section className="panel data-section" id="composition">
      <div className="data-section-title">
        <div>
          <p className="eyebrow">POOL COMPOSITION</p>
          <h2>选股数据集合成</h2>
        </div>
        <span>{snapshots.length} 个股票池</span>
      </div>
      <p className="page-description">
        按冻结股票池成员发现已发布的单标的数据集，以最近成交额为流动性排序，取前 N
        只合成为一个只读多标的日线数据集。合成不复制、不改写来源批次；回测页会显示数据集与来源质量报告。
      </p>
      {notice && (
        <p className="data-notice" role="status">
          {notice}
        </p>
      )}
      {composedDataset && (
        <p className="data-secondary">
          已生成数据集 <code>{composedDataset}</code>
        </p>
      )}
      {canManage && snapshots.length > 0 ? (
        <form action={composePoolDatasetAction} className="data-form">
          <label>
            股票池快照
            <select name="snapshot_id" required defaultValue={snapshots[0]?.snapshot_id}>
              {snapshots.map((snapshot) => (
                <option key={snapshot.snapshot_id} value={snapshot.snapshot_id}>
                  {snapshot.name} · v{snapshot.version} · {snapshot.members.length} 成员
                </option>
              ))}
            </select>
          </label>
          <label>
            起始日期
            <input type="date" name="start_date" required defaultValue="2025-10-31" />
          </label>
          <label>
            结束日期
            <input type="date" name="end_date" required defaultValue="2026-10-30" />
          </label>
          <label>
            入选数量
            <input
              type="number"
              name="selection_size"
              min={1}
              max={50}
              defaultValue={20}
              required
            />
          </label>
          <button type="submit">合成选股数据集</button>
        </form>
      ) : (
        !canManage && (
          <p className="data-empty">当前账号没有数据管理权限，无法合成选股数据集。</p>
        )
      )}
    </section>
  );
}