"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import styles from "./collector-scope.module.css";
import type { UniverseSnapshot } from "./universe-server";

export type DrawerCommit = { added: string[]; removed: string[] };

/** Modal picker for the collection scope. Pool members that already arrive
 * through a selected snapshot stay read-only here (the inheritance group owns
 * them); picks become explicit manual codes so they survive without the pool. */
export function InstrumentDrawer({
  snapshots,
  manualIds,
  poolIds,
  onClose,
  onCommit,
  restoreFocus,
}: {
  snapshots: readonly UniverseSnapshot[];
  manualIds: readonly string[];
  /** Codes already covered by inherited pools; visible but not selectable. */
  poolIds: ReadonlySet<string>;
  onClose: () => void;
  onCommit: (commit: DrawerCommit) => void;
  /** Called after the dialog closes so the opener regains focus. */
  restoreFocus: () => void;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  // The parent mounts the drawer per opening, so mount state starts from the
  // committed draft; later draft edits while open come from the drawer itself.
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(manualIds),
  );
  const [pool, setPool] = useState("");

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const frame = requestAnimationFrame(() => searchRef.current?.focus());
    return () => {
      cancelAnimationFrame(frame);
      document.body.style.overflow = previousOverflow;
    };
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        close();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(
        panelRef.current?.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex="0"]',
        ) ?? [],
      ).filter((element) => element.getClientRects().length > 0);
      const first = focusable[0];
      const last = focusable.at(-1);
      if (!first || !last) return;
      if (!panelRef.current?.contains(document.activeElement)) {
        event.preventDefault();
        first.focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // close is stable for the mounted drawer lifetime.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const normalized = query.trim().toLocaleLowerCase();
  const pools = useMemo(
    () => snapshots.filter((item) => !pool || item.snapshot_id === pool),
    [snapshots, pool],
  );
  const memberKey = (id: string, name: string) =>
    `${id} ${name}`.toLocaleLowerCase();
  const added = [...selected].filter((id) => !manualIds.includes(id));
  const removed = manualIds.filter((id) => !selected.has(id));
  const changes = added.length + removed.length;

  function close() {
    onClose();
    requestAnimationFrame(() => {
      restoreFocus();
      if (document.activeElement === document.body)
        document
          .querySelector<HTMLElement>(
            '#independent-collector [data-scope-field="instruments"]',
          )
          ?.focus();
    });
  }

  return (
    <div
      className={styles.drawerBackdrop}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <div
        ref={panelRef}
        className={styles.drawer}
        role="dialog"
        aria-modal="true"
        aria-labelledby="instrument-drawer-title"
        data-instrument-drawer="open"
      >
        <header className={styles.drawerHeader}>
          <div>
            <h2 id="instrument-drawer-title">标的选择清单</h2>
            <p>
              从已保存股票池或手工标的中选择；股票池成员已在继承范围内时不重复加入。
            </p>
          </div>
          <button
            type="button"
            className="button button-secondary"
            onClick={close}
          >
            关闭
          </button>
        </header>
        <div className={styles.drawerFilters}>
          <label>
            搜索标的
            <input
              ref={searchRef}
              type="search"
              aria-label="搜索标的清单"
              placeholder="代码或名称"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <label>
            股票池
            <select
              aria-label="清单股票池筛选"
              value={pool}
              onChange={(event) => setPool(event.target.value)}
            >
              <option value="">全部股票池</option>
              {snapshots.map((item) => (
                <option key={item.snapshot_id} value={item.snapshot_id}>
                  {item.name} · v{item.version}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className={styles.drawerBody}>
          {snapshots.length === 0 && manualIds.length === 0 && (
            <p className={styles.drawerEmpty}>
              暂无已保存股票池或手工标的；请先在数据目录保存股票池，或直接填写代码。
            </p>
          )}
          {pools.map((item) => {
            const members = item.members.filter((member) =>
              memberKey(
                member.instrument.instrument_id,
                member.instrument.name,
              ).includes(normalized),
            );
            if (!members.length) return null;
            return (
              <section
                key={item.snapshot_id}
                className={styles.drawerGroup}
                aria-label={`股票池 ${item.name}`}
              >
                <h3>
                  {item.name}
                  <small>
                    v{item.version} · {item.members.length} 个成员
                  </small>
                </h3>
                <div className={styles.drawerMembers}>
                  {members.map((member) => {
                    const id = member.instrument.instrument_id;
                    const inherited = poolIds.has(id);
                    return (
                      <label
                        key={id}
                        className={`${styles.drawerOption} ${inherited ? styles.inheritedOption : ""}`}
                        data-drawer-member={id}
                      >
                        <input
                          type="checkbox"
                          checked={inherited || selected.has(id)}
                          disabled={inherited}
                          onChange={(event) => {
                            const next = new Set(selected);
                            if (event.target.checked) next.add(id);
                            else next.delete(id);
                            setSelected(next);
                          }}
                        />
                        <span>
                          {member.instrument.name}
                          <small>
                            {id}
                            {inherited ? " · 已通过股票池包含" : ""}
                          </small>
                        </span>
                      </label>
                    );
                  })}
                </div>
              </section>
            );
          })}
          {manualIds.length > 0 && (
            <section className={styles.drawerGroup} aria-label="手工标的">
              <h3>
                手工标的
                <small>{manualIds.length} 个</small>
              </h3>
              <div className={styles.drawerMembers}>
                {manualIds
                  .filter((id) => id.toLocaleLowerCase().includes(normalized))
                  .map((id) => (
                    <label
                      key={id}
                      className={styles.drawerOption}
                      data-drawer-member={id}
                    >
                      <input
                        type="checkbox"
                        checked={selected.has(id)}
                        onChange={(event) => {
                          const next = new Set(selected);
                          if (event.target.checked) next.add(id);
                          else next.delete(id);
                          setSelected(next);
                        }}
                      />
                      <span>
                        {id}
                        <small>手工输入</small>
                      </span>
                    </label>
                  ))}
              </div>
            </section>
          )}
        </div>
        <footer className={styles.drawerFooter}>
          <span role="status">
            {changes
              ? `待变更：加入 ${added.length} 个、移除 ${removed.length} 个`
              : "选择结果与当前草稿一致"}
          </span>
          <div>
            <button
              type="button"
              className="button button-secondary"
              onClick={close}
            >
              取消
            </button>
            <button
              type="button"
              className="button button-primary"
              disabled={!changes}
              onClick={() => {
                onCommit({ added, removed });
                close();
              }}
            >
              加入本次范围
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
