import type { Metadata } from "next";
import type { ReactNode } from "react";
import { currentIdentity } from "../features/auth/server";
import { AppShell } from "../features/workbench/app-shell";
import { accessibleWorkspaces } from "../features/workbench/workspaces-server";
import "./globals.css";

export const metadata: Metadata = {
  title: "QTrade",
  description: "专业交易分析与量化研究平台",
};

export const dynamic = "force-dynamic";

export default async function RootLayout({
  children,
}: {
  children: ReactNode;
}) {
  const { authenticated, workspace } = await currentIdentity();
  const choices = authenticated
    ? await accessibleWorkspaces()
    : { workspaces: [], selectedId: "" };
  return (
    <html lang="zh-CN">
      <body>
        <AppShell
          authenticated={authenticated}
          workspace={workspace}
          workspaces={choices.workspaces}
          selectedWorkspaceId={choices.selectedId}
        >
          {children}
        </AppShell>
      </body>
    </html>
  );
}
