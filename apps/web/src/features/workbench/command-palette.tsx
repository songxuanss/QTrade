"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { searchCommandInstruments } from "../../app/command-actions";
import type { MarketInstrument } from "../auth/server";
import type { PublicWorkspace } from "../auth/public";
import { searchablePages } from "./command-permissions";

type Result = {
  key: string;
  title: string;
  detail: string;
  kind: "instrument" | "page";
  href: string;
};

export function CommandPalette({
  authenticated,
  role,
}: {
  authenticated: boolean;
  role: PublicWorkspace["role"] | null;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [searchResult, setSearchResult] = useState<{
    query: string;
    instruments: MarketInstrument[];
    error: boolean;
  } | null>(null);
  const [active, setActive] = useState(0);
  useEffect(() => {
    function shortcut(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        if (authenticated) setOpen((value) => !value);
      }
      if (event.key === "Escape") setOpen(false);
    }
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, [authenticated]);

  useEffect(() => {
    const term = query.trim();
    if (!open || !authenticated || term.length < 1) return;
    let current = true;
    const timer = setTimeout(() => {
      void searchCommandInstruments(term)
        .then((result) => {
          if (!current) return;
          setSearchResult({ query: term, ...result });
          setActive(0);
        })
        .catch(() => {
          if (!current) return;
          setSearchResult({ query: term, instruments: [], error: true });
        });
    }, 120);
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [authenticated, open, query]);

  const normalized = query.trim().toLocaleLowerCase();
  const resultIsCurrent =
    searchResult?.query.toLocaleLowerCase() === normalized;
  const instruments = resultIsCurrent ? searchResult.instruments : [];
  const searchError = resultIsCurrent && searchResult.error;
  const loading = open && normalized.length > 0 && !resultIsCurrent;
  const results: Result[] = [
    ...instruments.map((item) => ({
      key: `instrument:${item.instrument_id}`,
      title: item.instrument_id,
      detail: `${item.name} · ${item.market} · ${item.exchange}`,
      kind: "instrument" as const,
      href: `/workspace/market?instrument=${encodeURIComponent(item.instrument_id)}`,
    })),
    ...(role ? searchablePages(role, normalized) : [])
      .slice(0, 5)
      .map((item) => ({
        key: `page:${item.href}`,
        title: item.label,
        detail: item.href,
        kind: "page" as const,
        href: item.href,
      })),
  ];

  function select(result: Result | undefined) {
    if (!result) return;
    setOpen(false);
    setQuery("");
    router.push(result.href);
  }

  if (!authenticated) return null;
  return (
    <>
      <button
        className="command-trigger"
        type="button"
        onClick={() => setOpen(true)}
        aria-label="打开命令面板"
      >
        <span>搜索工作区</span>
        <kbd>⌘ K</kbd>
      </button>
      {open && (
        <div
          className="command-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setOpen(false);
          }}
        >
          <section
            className="command-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="command-title"
          >
            <h2 id="command-title" className="sr-only">
              命令面板
            </h2>
            <div className="command-input-wrap">
              <span aria-hidden="true">⌕</span>
              <input
                autoFocus
                aria-label="搜索 Instrument、Strategy、Backtest Run、Order 和 Alert"
                placeholder="搜索 Instrument、Strategy、Backtest Run、Order 和 Alert…"
                value={query}
                onChange={(event) => setQuery(event.target.value.slice(0, 128))}
                onKeyDown={(event) => {
                  if (event.key === "ArrowDown") {
                    event.preventDefault();
                    setActive((value) =>
                      Math.max(0, Math.min(value + 1, results.length - 1)),
                    );
                  }
                  if (event.key === "ArrowUp") {
                    event.preventDefault();
                    setActive((value) => Math.max(value - 1, 0));
                  }
                  if (event.key === "Enter") {
                    event.preventDefault();
                    select(results[active]);
                  }
                }}
              />
              <kbd>ESC</kbd>
            </div>
            <div
              className="command-results"
              role="listbox"
              aria-label="搜索结果"
            >
              {loading && <p role="status">正在搜索当前工作区…</p>}
              {searchError && (
                <p role="alert">暂时无法搜索工作区资源，请稍后重试。</p>
              )}
              {!loading && !searchError && query && results.length === 0 && (
                <p role="status">没有找到当前有权访问的资源。</p>
              )}
              {results.map((result, index) => (
                <Link
                  key={result.key}
                  role="option"
                  aria-selected={active === index}
                  className={`command-result ${active === index ? "active" : ""}`}
                  href={result.href}
                  onClick={() => {
                    setOpen(false);
                    setQuery("");
                  }}
                >
                  <span className="command-kind">
                    {result.kind === "instrument" ? "INSTRUMENT" : "工作区"}
                  </span>
                  <span className="command-copy">
                    <strong>{result.title}</strong>
                    <small>{result.detail}</small>
                  </span>
                  <span className="command-enter" aria-hidden="true">
                    ↵
                  </span>
                </Link>
              ))}
            </div>
            <footer className="command-footer">
              <span>↑ ↓ 选择</span>
              <span>Enter 打开</span>
              <span>搜索遵循当前工作区权限</span>
            </footer>
          </section>
        </div>
      )}
    </>
  );
}
