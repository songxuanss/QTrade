"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import styles from "./workbench-tabs.module.css";

type WorkspacePanel = {
  key: string;
  title: string;
  description?: string;
  content: ReactNode;
};

export function DataWorkbenchRefresh() {
  return (
    <button
      type="button"
      className="button button-secondary"
      onClick={() => location.reload()}
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
  compact = false,
}: {
  label: string;
  queryKey: string;
  initialKey: string;
  panels: WorkspacePanel[];
  anchors?: Record<string, string>;
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
      const key =
        anchors[url.hash.slice(1)] ??
        url.searchParams.get(queryKey) ??
        initialKey;
      if (panels.some((p) => p.key === key)) setSelected(key);
      else setSelected(initialKey);
    };
    sync();
    window.addEventListener("popstate", sync);
    window.addEventListener("hashchange", sync);
    return () => {
      window.removeEventListener("popstate", sync);
      window.removeEventListener("hashchange", sync);
    };
  }, [anchors, initialKey, panels, queryKey]);

  function select(index: number, focus = false) {
    const key = panels[index].key;
    setSelected(key);
    const url = new URL(location.href);
    url.searchParams.set(queryKey, key);
    url.hash = "";
    window.history.pushState(null, "", url);
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
