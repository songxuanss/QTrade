import { notFound } from "next/navigation";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PermissionState,
} from "../../components/ui/states";

export default function DesignSystem() {
  if (process.env.NODE_ENV === "production") notFound();
  return (
    <div className="workspace-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">DEVELOPMENT PREVIEW</p>
          <h1>界面状态</h1>
        </div>
      </div>
      <div className="state-preview-grid">
        <section className="panel">
          <h2 className="sr-only">空数据状态</h2>
          <EmptyState title="暂无数据" description="选择数据源后开始研究。" />
        </section>
        <section className="panel">
          <PermissionState />
        </section>
        <section className="panel">
          <ErrorState />
        </section>
        <section className="panel">
          <LoadingState label="正在加载数据" />
        </section>
      </div>
    </div>
  );
}
