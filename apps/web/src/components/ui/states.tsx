import type { ReactNode } from "react";
import { Icon, type IconName } from "./icon";

export function EmptyState({
  title,
  description,
  icon = "folder",
  action,
  headingLevel = 3,
}: {
  title: string;
  description: string;
  icon?: IconName;
  action?: ReactNode;
  headingLevel?: 2 | 3;
}) {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <div className="empty-state">
      <span className="empty-icon">
        <Icon name={icon} size={23} />
      </span>
      <Heading>{title}</Heading>
      <p>{description}</p>
      {action}
    </div>
  );
}

export function PermissionState({
  title = "需要工作区访问权限",
  description = "登录后查看授权工作区中的数据与操作。",
}: {
  title?: string;
  description?: string;
}) {
  return (
    <div className="permission-state" role="status">
      <span className="empty-icon">
        <Icon name="lock" size={23} />
      </span>
      <h2>{title}</h2>
      <p>{description}</p>
      <span className="badge badge-neutral">仅授权成员可访问</span>
    </div>
  );
}

export function ErrorState({
  retry,
  title = "暂时无法加载",
  description = "请重试。已保存的数据不会受到影响。",
}: {
  retry?: () => void;
  title?: string;
  description?: string;
}) {
  return (
    <div className="error-state" role="alert">
      <span className="empty-icon">
        <Icon name="alert" size={23} />
      </span>
      <h2>{title}</h2>
      <p>{description}</p>
      {retry && (
        <button className="button button-secondary" onClick={retry}>
          重新加载
        </button>
      )}
    </div>
  );
}

export function LoadingState({ label = "正在加载工作区" }: { label?: string }) {
  return (
    <div role="status" aria-label={label} className="loading-state">
      <span className="sr-only">{label}</span>
      <div className="skeleton skeleton-title" />
      <div className="metric-grid">
        {[0, 1, 2, 3].map((item) => (
          <div className="skeleton skeleton-card" key={item} />
        ))}
      </div>
      <div className="skeleton skeleton-panel" />
    </div>
  );
}
