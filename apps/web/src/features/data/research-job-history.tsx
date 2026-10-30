"use client";

import { useState, type ReactNode } from "react";
import type { ResearchJob } from "./research-collection-types";
import styles from "./research-job-history.module.css";

/** Keeps loaded task history searchable without letting it expand the workspace.
 * Search is local to the API's recent-task snapshot; it does not claim to query
 * the full archive. Row actions retain their owning panel's authorization.
 */
export function ResearchJobHistory({
  name,
  dateAxis,
  jobs,
  instrumentNames,
  children,
}: {
  name: string;
  dateAxis: string;
  jobs: ResearchJob[];
  instrumentNames: Record<string, string>;
  children: (job: ResearchJob) => ReactNode;
}) {
  const [search, setSearch] = useState("");
  const query = search.trim().toLocaleLowerCase();
  const visible = jobs.filter((job) =>
    [
      job.request_id,
      job.request.source,
      job.request.start,
      job.request.end,
      ...job.request.instrument_ids,
      ...job.request.instrument_ids.map((id) => instrumentNames[id] ?? ""),
    ]
      .join(" ")
      .toLocaleLowerCase()
      .includes(query),
  );
  return (
    <div className={styles.history}>
      <div className={styles.toolbar}>
        <label className={styles.search}>
          搜索{name}近期任务
          <input
            type="search"
            value={search}
            placeholder="简称、代码、来源、日期或任务编号"
            maxLength={128}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        <p role="status">
          显示 {visible.length} / {jobs.length} 条已加载任务
        </p>
      </div>
      <p className={styles.hint}>
        仅搜索服务返回的近期记录，不代表全部历史。表格内可滚动查看，刷新任务可更新列表。
      </p>
      <div
        className={styles.viewport}
        role="region"
        aria-label={`${name}任务列表`}
        tabIndex={0}
      >
        <table>
          <caption>最近{name}任务</caption>
          <thead>
            <tr>
              <th scope="col">证券</th>
              <th scope="col">来源 / {dateAxis}</th>
              <th scope="col">状态</th>
              <th scope="col">操作</th>
            </tr>
          </thead>
          <tbody>{visible.map(children)}</tbody>
        </table>
        {!visible.length && (
          <p className={styles.empty}>
            {query ? "没有匹配的近期任务，请调整搜索。" : "暂无近期任务。"}
          </p>
        )}
      </div>
    </div>
  );
}
