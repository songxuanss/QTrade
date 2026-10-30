import Link from "next/link";
import { Icon } from "../components/ui/icon";

export default function NotFound() {
  return (
    <div className="workspace-page">
      <section className="panel not-found-panel">
        <span className="empty-icon">
          <Icon name="folder" size={26} />
        </span>
        <p className="eyebrow">404 · RESOURCE NOT FOUND</p>
        <h1>页面不存在</h1>
        <p className="page-description">链接可能已失效。返回工作台继续研究。</p>
        <Link className="button button-secondary" href="/">
          返回工作台
          <Icon name="arrow" size={14} />
        </Link>
      </section>
    </div>
  );
}
