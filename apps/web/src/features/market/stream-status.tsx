"use client";

import { useEffect, useState } from "react";

type StreamMessage = {
  status:
    | "healthy"
    | "degraded"
    | "disconnected"
    | "reconnecting"
    | "unconfigured"
    | "unavailable";
  latest_lag_ms?: number | null;
  reconnect_count?: number;
};

const labels: Record<StreamMessage["status"] | "connecting", string> = {
  connecting: "连接行情推送…",
  healthy: "行情推送正常",
  degraded: "行情延迟",
  disconnected: "行情已断开",
  reconnecting: "正在重连行情…",
  unconfigured: "实时数据源未配置",
  unavailable: "行情状态暂不可用",
};
type DisplayStatus = StreamMessage["status"] | "connecting";

export function MarketStreamStatus({
  workspaceId,
  websocketOrigin,
}: {
  workspaceId: string;
  websocketOrigin: string;
}) {
  const [status, setStatus] = useState<StreamMessage | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    let socket: WebSocket | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stableTimer: ReturnType<typeof setTimeout> | undefined;
    let retries = 0;
    const connect = () => {
      if (!active) return;
      if (retries) setStatus({ status: "reconnecting" });
      const endpoint = new URL(
        `/api/v1/workspaces/${workspaceId}/market-streams/ws/mock-feed/MOCK%3AABC`,
        websocketOrigin,
      );
      endpoint.protocol = endpoint.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(endpoint);
      socket.onopen = () => {
        stableTimer = setTimeout(() => {
          retries = 0;
        }, 30_000);
        setAttempt((value) => value + 1);
      };
      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data) as StreamMessage;
          if (message && typeof message.status === "string") setStatus(message);
        } catch {
          setStatus({ status: "unavailable" });
        }
      };
      socket.onerror = () => socket?.close();
      socket.onclose = () => {
        if (!active) return;
        if (stableTimer) clearTimeout(stableTimer);
        setStatus({ status: "reconnecting" });
        const delay = Math.min(1000 * 2 ** retries, 10_000);
        retries = Math.min(retries + 1, 5);
        timer = setTimeout(connect, delay);
      };
    };
    connect();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
      if (stableTimer) clearTimeout(stableTimer);
      socket?.close();
    };
  }, [workspaceId, websocketOrigin]);

  const displayStatus: DisplayStatus = status?.status ?? "connecting";
  const label = labels[displayStatus];
  const delay = status?.latest_lag_ms;
  return (
    <div
      className={`market-stream-status ${displayStatus}`}
      role="status"
      aria-live="polite"
      aria-label={`行情连接状态：${label}`}
      title={
        status?.reconnect_count
          ? `累计重连 ${status.reconnect_count} 次`
          : undefined
      }
    >
      <span aria-hidden="true" />
      {label}
      {typeof delay === "number" ? ` · ${delay} ms` : ""}
      <span className="sr-only">连接编号 {attempt}</span>
    </div>
  );
}
