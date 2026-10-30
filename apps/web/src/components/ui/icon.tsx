import type { CSSProperties } from "react";

const paths = {
  grid: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
  market: "M3 17l5-6 4 3 8-10 M15 4h5v5 M3 21h18",
  strategy: "M8 3H5v18h3 M16 3h3v18h-3 M10 8l-3 4 3 4 M14 8l3 4-3 4",
  backtest: "M4 19V9 M10 19V4 M16 19v-7 M3 21h18 M19 8l2-3",
  trade: "M3 7h17l-4-4 M21 17H4l4 4",
  shield: "M12 3l8 3v6c0 5-8 9-8 9s-8-4-8-9V6z M8 12l3 3 5-6",
  bell: "M5 17h14l-2-3V9a5 5 0 0 0-10 0v5z M10 21h4",
  data: "M4 5c0-3 16-3 16 0s-16 3-16 0z M4 5v7c0 3 16 3 16 0V5 M4 12v7c0 3 16 3 16 0v-7",
  settings:
    "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M12 2v3 M12 19v3 M2 12h3 M19 12h3 M5 5l2 2 M17 17l2 2 M5 19l2-2 M17 7l2-2",
  arrow: "M5 19L19 5 M7 5h12v12",
  search: "M10.5 3a7.5 7.5 0 1 0 0 15 7.5 7.5 0 0 0 0-15 M16 16l5 5",
  lock: "M6 10h12v11H6z M8 10V7a4 4 0 0 1 8 0v3",
  plus: "M12 4v16 M4 12h16",
  check: "M5 12l4 4L19 6",
  chevron: "M9 5l7 7-7 7",
  menu: "M3 6h18 M3 12h18 M3 18h18",
  close: "M5 5l14 14 M19 5L5 19",
  alert: "M12 3L2 21h20z M12 9v5 M12 17v1",
  clock: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20 M12 6v6l4 2",
  folder: "M3 6h7l2 3h9v11H3z M3 6V4h7l2 2h9v3",
  activity: "M2 12h5l3-8 4 16 3-8h5",
} as const;

export type IconName = keyof typeof paths;

export function Icon({
  name,
  size = 18,
  style,
}: {
  name: IconName;
  size?: number;
  style?: CSSProperties;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={style}
    >
      <path d={paths[name]} />
    </svg>
  );
}
