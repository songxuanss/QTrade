import Link from "next/link";
import { CampaignPanel } from "./campaign-panel";
import {
  customUniverseAction,
  resolveUniverseAction,
} from "../../app/workspace/universe-actions";
import type { UniverseDefinition, UniverseSnapshot } from "./universe-server";

const categories = {
  industry: "行业",
  theme: "主题",
  index_members: "指数成分",
};
const date = (value: string) =>
  new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));

export function UniversePanel({
  catalog,
  snapshot,
  recent,
  canManage,
  search,
  category,
  offset,
  memberPage,
  result,
  periods,
}: {
  catalog: { items: UniverseDefinition[]; total: number; available: boolean };
  snapshot: UniverseSnapshot | null;
  recent: UniverseSnapshot[];
  canManage: boolean;
  search: string;
  category: string;
  offset: number;
  memberPage: number;
  result?: string;
  periods: string[];
}) {
  const href = (next: number) =>
    `/workspace/data?${new URLSearchParams({ universe_search: search, universe_category: category, universe_offset: String(next) })}#universe-catalog`;
  const memberOffset = memberPage * 30;
  const members =
    snapshot?.members.slice(memberOffset, memberOffset + 30) ?? [];
  return (
    <section
      className="panel universe-workbench"
      id="universe-catalog"
      aria-labelledby="universe-title"
    >
      <div className="universe-heading">
        <div>
          <p className="eyebrow">MARKET UNIVERSES</p>
          <h2 id="universe-title">股票池与板块</h2>
          <p className="page-description">
            查看行业、主题和指数成分，保存可追溯的成员快照。
          </p>
        </div>
        <span className="universe-badge">当前成员 · 历史成分未核验</span>
      </div>
      {result && (
        <p className="data-notice" role="alert">
          {result === "permission"
            ? "当前账号没有股票池管理权限。"
            : "暂时无法解析股票池，请稍后重试。"}
        </p>
      )}
      <details className="universe-selector" open={!snapshot}>
        <summary>选择行业、主题或指数成分</summary>
        <nav className="universe-shortcuts" aria-label="重点股票池入口">
          <Link href="/workspace/data?universe_search=半导体&universe_category=industry#universe-catalog">
            半导体行业
          </Link>
          <Link href="/workspace/data?universe_search=AI芯片&universe_category=theme#universe-catalog">
            AI 芯片主题
          </Link>
          <Link href="/workspace/data?universe_search=沪深300&universe_category=index_members#universe-catalog">
            沪深 300 成分
          </Link>
        </nav>
        <form method="get" action="/workspace/data" className="universe-search">
          <label>
            搜索板块
            <input
              name="universe_search"
              defaultValue={search}
              placeholder="半导体、AI、沪深300"
              maxLength={100}
            />
          </label>
          <label>
            股票池类别
            <select
              aria-label="股票池类别"
              name="universe_category"
              defaultValue={category}
            >
              <option value="">全部类别</option>
              {Object.entries(categories).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <button className="button button-secondary" type="submit">
            搜索股票池
          </button>
        </form>
        {!catalog.available ? (
          <p role="alert" className="data-notice">
            股票池来源暂不可用，现有单股采集仍可使用。
          </p>
        ) : (
          <>
            <div
              className="universe-grid"
              role="region"
              aria-label="股票池目录"
              tabIndex={0}
            >
              {catalog.items.map((item) => (
                <article className="universe-card" key={item.universe_id}>
                  <span className="eyebrow">{categories[item.category]}</span>
                  <h3>
                    {item.name.replace(
                      /^A股-(申万行业|申万二级|热门概念|概念板块|指数成分)-/,
                      "",
                    )}
                  </h3>
                  <p>
                    {item.source_status === "ambiguous"
                      ? "来源代码对应多个分类，暂不可解析。"
                      : "Mairui 当前成员，解析后可查看来源与差异。"}
                  </p>
                  {canManage && (
                    <form action={resolveUniverseAction}>
                      <input
                        type="hidden"
                        name="universe_id"
                        value={item.universe_id}
                      />
                      <button
                        className="button button-secondary"
                        disabled={item.source_status !== "available"}
                      >
                        预览并保存成员
                      </button>
                      <button
                        className="button"
                        name="open_download"
                        value="true"
                        disabled={item.source_status !== "available"}
                      >
                        下载全部成员
                      </button>
                    </form>
                  )}
                </article>
              ))}
            </div>
            {catalog.items.length === 0 && (
              <p>没有匹配股票池，请更换关键词。</p>
            )}
            <nav className="universe-pagination" aria-label="股票池分页">
              <span>共 {catalog.total} 个分类</span>
              {offset > 0 && (
                <Link href={href(Math.max(0, offset - 12))}>上一页</Link>
              )}
              {offset + 12 < catalog.total && (
                <Link href={href(offset + 12)}>下一页</Link>
              )}
            </nav>
          </>
        )}
      </details>
      {canManage && (
        <details className="universe-custom">
          <summary>创建我的股票池</summary>
          <form action={customUniverseAction} className="universe-search">
            <label>
              股票池名称
              <input
                name="name"
                maxLength={100}
                required
                placeholder="我的重点股票"
              />
            </label>
            <label>
              股票代码
              <textarea
                name="instrument_ids"
                required
                placeholder="601288.SH, 600519.SH"
                rows={2}
              />
            </label>
            <button className="button" type="submit">
              保存自定义股票池
            </button>
          </form>
        </details>
      )}
      {recent.length > 0 && (
        <details className="universe-recent">
          <summary>已保存股票池（最近 {recent.length} 个版本）</summary>
          <ul>
            {recent.map((item) => (
              <li key={item.snapshot_id}>
                <Link
                  href={`/workspace/data?universe_snapshot=${item.snapshot_id}#universe-members`}
                >
                  {item.name} · v{item.version} · {item.members.length} 个成员
                </Link>
              </li>
            ))}
          </ul>
        </details>
      )}
      <CampaignPanel
        key={snapshot?.snapshot_id ?? "none"}
        snapshot={snapshot}
        recent={recent}
        canManage={canManage}
        periods={periods}
      />
      {snapshot && (
        <div id="universe-members" className="universe-members">
          <div className="universe-heading">
            <div>
              <h3>{snapshot.name}</h3>
              <p>
                v{snapshot.version} · {snapshot.members.length} 个成员 ·{" "}
                {snapshot.market_scope.join(" / ")}
              </p>
              <p className="page-description">
                观察时间：{date(snapshot.observed_at)}
                。该快照不会随来源更新而变化。
              </p>
            </div>
            {canManage && (
              <Link className="button" href="#pool-download">
                下载本板块全部成员
              </Link>
            )}
          </div>
          {snapshot.reference_issues.length > 0 && (
            <details className="universe-conflicts">
              <summary>
                {snapshot.reference_issues.length} 项参考数据差异
              </summary>
              <p>
                保留成分接口的名称与成员，目录缺失或名称差异尚未核验，不自动判断停牌或退市。
              </p>
              <ul>
                {snapshot.reference_issues.map((issue) => (
                  <li key={`${issue.instrument_id}/${issue.code}`}>
                    {issue.instrument_id}：{issue.member_name}
                    {issue.directory_name
                      ? `（目录：${issue.directory_name}）`
                      : "（股票目录未找到）"}
                  </li>
                ))}
              </ul>
            </details>
          )}
          <div
            className="universe-member-grid"
            role="region"
            aria-label="股票池成员"
            tabIndex={0}
          >
            {members.map((member) => (
              <article
                className="universe-member"
                key={member.instrument.instrument_id}
              >
                <div>
                  <strong>{member.instrument.name}</strong>
                  <span>{member.instrument.instrument_id}</span>
                </div>
                <Link
                  href={`/workspace/market?instrument=${member.instrument.instrument_id}`}
                >
                  查看行情
                </Link>
              </article>
            ))}
          </div>
          <nav className="universe-pagination" aria-label="成员分页">
            <span>第 {memberPage + 1} 页 · 每页 30 个成员</span>
            {memberPage > 0 && (
              <Link
                href={`/workspace/data?universe_snapshot=${snapshot.snapshot_id}&universe_member_page=${memberPage - 1}#universe-members`}
              >
                上一页
              </Link>
            )}
            {memberOffset + 30 < snapshot.members.length && (
              <Link
                href={`/workspace/data?universe_snapshot=${snapshot.snapshot_id}&universe_member_page=${memberPage + 1}#universe-members`}
              >
                下一页
              </Link>
            )}
          </nav>
        </div>
      )}
    </section>
  );
}
