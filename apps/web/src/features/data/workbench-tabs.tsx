"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import styles from "./workbench-tabs.module.css";

/** Any component that rewrites the URL for presentation-only navigation
 * announces it here, so tab strips and URL-derived panel state re-read the
 * address bar without a full router round-trip. */
export const WORKBENCH_NAVIGATION_EVENT = "workbench-navigation";

type WorkspacePanel = {
  key: string;
  title: string;
  description?: string;
  content: ReactNode;
};

/** Legacy query values keep old deep links working: a plain string maps to a
 * fixed key, a function resolves conditionally (e.g. collect_view=results). */
export type PanelAliases = Record<string, string | ((url: URL) => string)>;

export function DataWorkbenchRefresh() {
  const router = useRouter();
  // Refresh the Server Component tree without unmounting client forms, so
  // server results update while an unfinished provider draft is retained.
  return (
    <button
      type="button"
      className="button button-secondary"
      onClick={() => router.refresh()}
    >
      刷新数据
    </button>
  );
}

/** Keep panels mounted so switching tools cannot discard an in-progress form.
 * The URL stores navigation only: no credentials, provider state or draft inputs.
 * Old fragment links still reveal their owning panel before browser scrolling. */
export function WorkbenchTabs({
  label,
  queryKey,
  initialKey,
  panels,
  anchors = {},
  aliases = {},
  compact = false,
}: {
  label: string;
  queryKey: string;
  initialKey: string;
  panels: WorkspacePanel[];
  anchors?: Record<string, string>;
  aliases?: PanelAliases;
  compact?: boolean;
}) {
  const id = useId();
  const [selected, setSelected] = useState(initialKey);
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const strip = useRef<HTMLDivElement>(null);
  const active = panels.some((p) => p.key === selected)
    ? selected
    : panels[0].key;
  useEffect(() => {
    const list = strip.current;
    const button =
      buttons.current[panels.findIndex((panel) => panel.key === active)];
    if (!list || !button) return;
    // Reveal the current tab on deep links/back navigation too. Move only the
    // horizontal strip, preserving the user's document/record scroll position.
    const item = button.getBoundingClientRect();
    const frame = list.getBoundingClientRect();
    if (item.left < frame.left) list.scrollLeft += item.left - frame.left;
    else if (item.right > frame.right)
      list.scrollLeft += item.right - frame.right;
  }, [active, panels]);
  useEffect(() => {
    const sync = () => {
      const url = new URL(location.href);
      const raw = url.searchParams.get(queryKey);
      const alias = raw === null ? undefined : aliases[raw];
      const key =
        anchors[url.hash.slice(1)] ??
        (typeof alias === "function" ? alias(url) : alias) ??
        raw ??
        initialKey;
      if (panels.some((p) => p.key === key)) setSelected(key);
      else setSelected(initialKey);
    };
    sync();
    window.addEventListener("popstate", sync);
    window.addEventListener("hashchange", sync);
    window.addEventListener(WORKBENCH_NAVIGATION_EVENT, sync);
    return () => {
      window.removeEventListener("popstate", sync);
      window.removeEventListener("hashchange", sync);
      window.removeEventListener(WORKBENCH_NAVIGATION_EVENT, sync);
    };
  }, [aliases, anchors, initialKey, panels, queryKey]);

  function select(index: number, focus = false) {
    const key = panels[index].key;
    setSelected(key);
    const url = new URL(location.href);
    url.searchParams.set(queryKey, key);
    url.hash = "";
    window.history.pushState(null, "", url);
    // URL-derived panel state (e.g. the collector's create/results half) must
    // follow tab clicks even though pushState fires no native event.
    window.dispatchEvent(new Event(WORKBENCH_NAVIGATION_EVENT));
    if (focus) {
      buttons.current[index]?.focus();
      buttons.current[index]?.scrollIntoView({
        block: "nearest",
        inline: "nearest",
      });
    }
  }

  return (
    <div className={styles.workspace} data-workbench-tabs={queryKey}>
      <div className={compact ? styles.secondary : styles.primary}>
        <div
          ref={strip}
          className={styles.tabs}
          role="tablist"
          aria-label={label}
        >
          {panels.map((panel, index) => (
            <button
              key={panel.key}
              ref={(node) => {
                buttons.current[index] = node;
              }}
              id={`${id}-tab-${panel.key}`}
              role="tab"
              type="button"
              aria-selected={active === panel.key}
              aria-controls={`${id}-panel-${panel.key}`}
              tabIndex={active === panel.key ? 0 : -1}
              className={styles.tab}
              onClick={() => select(index)}
              onKeyDown={(event) => {
                let next: number;
                if (event.key === "ArrowRight")
                  next = (index + 1) % panels.length;
                else if (event.key === "ArrowLeft")
                  next = (index + panels.length - 1) % panels.length;
                else if (event.key === "Home") next = 0;
                else if (event.key === "End") next = panels.length - 1;
                else return;
                event.preventDefault();
                select(next, true);
              }}
            >
              {panel.title}
            </button>
          ))}
        </div>
      </div>
      {panels.map((panel) => (
        <section
          key={panel.key}
          id={`${id}-panel-${panel.key}`}
          role="tabpanel"
          aria-labelledby={`${id}-tab-${panel.key}`}
          hidden={active !== panel.key}
          className={styles.panel}
          data-workbench-view={panel.key}
        >
          {panel.description && (
            <p className={styles.description}>{panel.description}</p>
          )}
          {panel.content}
        </section>
      ))}
    </div>
  );
}
