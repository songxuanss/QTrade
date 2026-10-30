import {
  currentSession,
  workspacePortfolioSummary,
} from "../features/auth/server";
import { Overview } from "../features/workbench/overview";

export default async function HomePage() {
  const authenticated = await currentSession();
  const portfolio = authenticated ? await workspacePortfolioSummary() : null;
  return (
    <Overview
      authenticated={authenticated}
      portfolio={portfolio?.summary ?? null}
      portfolioError={portfolio?.error ?? false}
      loginEnabled={Boolean(process.env.QTRADE_OIDC_ISSUER)}
    />
  );
}
