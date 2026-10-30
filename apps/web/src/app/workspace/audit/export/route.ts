import {
  auditFilterNames,
  auditParams,
  type AuditFilters,
} from "../../../../features/audit/server";
import { workspaceRequest } from "../../../../features/auth/workspace-request";
export async function GET(request: Request) {
  const filters: AuditFilters = {};
  const query = new URL(request.url).searchParams;
  for (const name of auditFilterNames)
    if (query.get(name)) filters[name] = query.get(name)!;
  delete filters.before;
  for (const key of ["start", "end"] as const)
    if (filters[key] && !/[zZ]|[+-]\d{2}:\d{2}$/.test(filters[key]))
      filters[key] += "Z";
  const response = await workspaceRequest(
    "audit/export",
    "GET",
    undefined,
    auditParams(filters),
  );
  if (!response?.ok)
    return new Response(
      response?.status === 403
        ? "无权导出审计记录。"
        : "导出未完成，请检查时间范围（最长 31 天、最多 5,000 条）与当前权限。",
      {
        status: response?.status ?? 503,
        headers: {
          "Cache-Control": "no-store",
          "Content-Type": "text/plain; charset=utf-8",
          "X-Content-Type-Options": "nosniff",
        },
      },
    );
  return new Response(await response.arrayBuffer(), {
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": 'attachment; filename="qtrade-audit.csv"',
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
}
