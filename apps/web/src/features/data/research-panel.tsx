import Link from "next/link";
import type { DataTask } from "../auth/server";
import type { ResearchDataset } from "./research-server";
import { publishResearchAction } from "../../app/workspace/data-actions";
export function ResearchDataPanel({
  tasks,
  datasets,
  available,
  canManage,
  result,
}: {
  tasks: DataTask[];
  datasets: ResearchDataset[];
  available: boolean;
  canManage: boolean;
  result?: string;
}) {
  const notices: Record<string, string> = {
    research_saved:
      "研究数据已生成。现在可以在市场终端查看历史 K 线；研究所需的数据完整性仍须核验。",
    research_incomplete: "下载尚未完成，请等待采集结束后再生成研究数据。",
    research_invalid:
      "该下载无法生成研究数据：需包含实际成交，且起止时间须对齐完整分钟。",
    research_error: "研究数据生成失败，请检查市场存储和对象存储服务后重试。",
  };
  const completed = tasks.filter(
    (task) => task.status === "completed" && task.granularity === "tick",
  );
  return (
    <section className="panel data-section" id="research-data">
      <div className="data-section-heading">
        <div>
          <p className="eyebrow">RESEARCH DATA</p>
          <h2>研究数据集</h2>
        </div>
        <span>{datasets.length} 项</span>
      </div>
      <p className="page-description">
        将已完成的逐笔下载聚合为 1 分钟 K
        线。原始批次保留，不填充没有成交的分钟；此处为历史数据。
      </p>
      {result && notices[result] && (
        <p
          className={`data-notice ${result === "research_saved" ? "success" : ""}`}
          role={result === "research_saved" ? "status" : "alert"}
        >
          {notices[result]}
        </p>
      )}
      {!available ? (
        <p className="data-empty">研究数据服务暂不可用。</p>
      ) : (
        <>
          {canManage && completed.length > 0 && (
            <form
              action={publishResearchAction}
              className="research-publish-form"
            >
              <label>
                已完成下载
                <select name="task_id" aria-label="已完成下载" required>
                  {completed.map((task) => (
                    <option key={task.task_id} value={task.task_id}>
                      {task.instrument_id} · {task.start.slice(0, 16)} —{" "}
                      {task.end.slice(0, 16)} UTC
                    </option>
                  ))}
                </select>
              </label>
              <button className="button button-primary" type="submit">
                生成 1 分钟研究数据
              </button>
            </form>
          )}
          {datasets.length ? (
            <div className="data-list">
              {datasets.map((dataset) => (
                <article
                  className="data-row research-dataset-row"
                  key={dataset.dataset_id}
                  data-dataset-id={dataset.dataset_id}
                >
                  <div className="data-row-main">
                    <strong>
                      {dataset.instrument_ids.length > 5
                        ? `${dataset.instrument_ids.slice(0, 5).join("、")} 等 ${dataset.instrument_ids.length} 个标的`
                        : dataset.instrument_ids.join("、")}
                    </strong>
                    <span>
                      {dataset.granularity} · {dataset.instrument_ids.length} 个标的 ·{" "}
                      {dataset.batch_ids.length} 个原始批次 ·{" "}
                      <code title={dataset.dataset_id}>
                        {dataset.dataset_id.slice(0, 8)}
                      </code>
                    </span>
                    <span className="data-period">
                      {dataset.start.slice(0, 16)} — {dataset.end.slice(0, 16)}{" "}
                      UTC
                    </span>
                  </div>
                  <div className="research-dataset-links">
                    <Link
                      className="button button-secondary"
                      href={`/workspace/data?quality_dataset=${dataset.dataset_id}`}
                    >
                      查看质量报告
                    </Link>
                    <Link
                      className="button button-secondary"
                      href={`/workspace/market?instrument=${encodeURIComponent(dataset.instrument_ids[0])}`}
                    >
                      查看历史行情
                    </Link>
                    <Link
                      className="button button-secondary"
                      href={`/workspace/backtests?dataset_id=${encodeURIComponent(dataset.dataset_id)}`}
                    >
                      配置回测
                    </Link>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <p className="data-empty">
              完成行情下载后可查看历史数据；逐笔数据需先生成分钟研究数据。
            </p>
          )}
        </>
      )}
    </section>
  );
}
