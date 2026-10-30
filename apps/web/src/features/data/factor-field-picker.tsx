"use client";

import { useState } from "react";
import type { FactorDefinition } from "./collector-types";
import styles from "./factor-field-picker.module.css";

const known = (value: string | undefined) =>
  value && value !== "unknown" ? value : "未知（来源未提供）";

const categoriesZh: Record<string, string> = {
  capital: "资金",
  dividend: "分红",
  growth: "成长",
  intraday: "盘中",
  momentum: "动量",
  quality: "质量",
  risk: "风险",
  scale: "规模",
  sentiment: "情绪",
  signal: "信号",
  valuation: "估值",
};
const categoryName = (value: string) =>
  categoriesZh[value] ? `${categoriesZh[value]} · ${value}` : known(value);

/** Controlled selection: onChange receives the complete set, including hidden
 * fields. Filtering never emits selection changes; metadata does not prove
 * history coverage or account rights.
 */
export function FactorFieldPicker({
  definitions,
  selected,
  onChange,
}: {
  definitions: FactorDefinition[];
  selected: string[];
  onChange: (fields: string[]) => void;
}) {
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const categories = [...new Set(definitions.map((d) => d.category))].sort();
  const query = search.trim().toLocaleLowerCase();
  const visible = definitions.filter(
    (d) =>
      (!category || d.category === category) &&
      [d.factor_id, d.name, d.description ?? ""].some((v) =>
        v.toLocaleLowerCase().includes(query),
      ),
  );
  const missing = selected.filter(
    (id) => !definitions.some((d) => d.factor_id === id),
  );
  function toggle(id: string, checked: boolean) {
    onChange(
      checked
        ? [...new Set([...selected, id])]
        : selected.filter((value) => value !== id),
    );
  }
  return (
    <div className={styles.picker}>
      <div className={styles.filters}>
        <label>
          搜索因子
          <input
            type="search"
            aria-label="搜索因子"
            placeholder="名称、代码或定义"
            value={search}
            onChange={(e) => {
              // View filters do not alter the frozen collection selection/preview.
              e.stopPropagation();
              setSearch(e.target.value);
            }}
          />
        </label>
        <label>
          因子分类
          <select
            aria-label="因子分类"
            value={category}
            onChange={(e) => {
              e.stopPropagation();
              setCategory(e.target.value);
            }}
          >
            <option value="">全部分类</option>
            {categories.map((value) => (
              <option key={value} value={value}>
                {categoryName(value)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className={styles.count} role="status">
        已选 {selected.length} 项 · 显示 {visible.length} / {definitions.length}{" "}
        项。筛选不会改变已选项。
      </p>
      {selected.length > 0 && (
        <div className={styles.selection} aria-label="已选因子">
          {selected.map((id) => (
            <button
              type="button"
              key={id}
              aria-label={`移除因子 ${id}`}
              onClick={() => toggle(id, false)}
            >
              {definitions.find((d) => d.factor_id === id)?.name ?? id} · {id} ×
            </button>
          ))}
        </div>
      )}
      {missing.length > 0 && (
        <p role="alert">
          当前目录未包含已选项：{missing.join("、")}
          。选择已保留，请核查或移除后重新预览。
        </p>
      )}
      <div
        className={styles.results}
        tabIndex={0}
        aria-label="历史因子字段列表"
      >
        {visible.map((field) => (
          <article
            key={field.factor_id}
            className={styles.card}
            data-factor-id={field.factor_id}
          >
            <label className={styles.choice}>
              <input
                type="checkbox"
                checked={selected.includes(field.factor_id)}
                disabled={
                  !field.available && !selected.includes(field.factor_id)
                }
                onChange={(e) => toggle(field.factor_id, e.target.checked)}
              />
              <span>
                {field.name}
                <small>
                  {field.factor_id} · {categoryName(field.category)}
                </small>
              </span>
            </label>
            {!field.available && (
              <p>{field.unavailable_reason ?? "当前不可选择"}</p>
            )}
            <details>
              <summary>查看定义与元数据</summary>
              <p>{field.description?.trim() || "来源未提供定义说明。"}</p>
              <dl>
                <dt>单位</dt>
                <dd>{known(field.unit)}</dd>
                <dt>更新频率</dt>
                <dd>{known(field.update_frequency)}</dd>
                <dt>计算版本</dt>
                <dd>{known(field.calc_version)}</dd>
                <dt>历史范围</dt>
                <dd>未由目录证明，需逐标的、逐区间验证。</dd>
              </dl>
            </details>
          </article>
        ))}
        {visible.length === 0 && (
          <p className={styles.empty}>
            没有匹配的因子。请调整搜索或分类；已选项仍保留。
          </p>
        )}
      </div>
    </div>
  );
}
