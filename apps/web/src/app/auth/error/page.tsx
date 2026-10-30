import { Icon } from "../../../components/ui/icon";
export default function LoginError() {
  return (
    <div className="workspace-page">
      <section className="panel not-found-panel">
        <span className="empty-icon">
          <Icon name="lock" size={26} />
        </span>
        <p className="eyebrow">IDENTITY & ACCESS</p>
        <h1>登录未完成</h1>
        <p className="page-description">
          登录请求已失效或身份验证未通过，请重新登录。
        </p>
        <a className="button button-secondary" href="/auth/login">
          重新登录
          <Icon name="arrow" size={14} />
        </a>
      </section>
    </div>
  );
}
