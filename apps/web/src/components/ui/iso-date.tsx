"use client";

import { useRef, useState } from "react";
import { Icon } from "./icon";

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

function normalize(value: string) {
  const digits = value.replace(/\D/g, "");
  if (digits.length !== 8) return value;
  return `${digits.slice(0, 4)}-${digits.slice(4, 6)}-${digits.slice(6)}`;
}

/** Date entry that always shows ISO `YYYY-MM-DD`, independent of the browser
 * locale. The native picker remains available through the calendar button. */
export function IsoDateInput({
  label,
  ariaLabel,
  name,
  value: controlledValue,
  defaultValue = "",
  onChange,
  required = false,
  disabled = false,
  className,
  dataField,
}: {
  label: string;
  ariaLabel?: string;
  name?: string;
  value?: string;
  defaultValue?: string;
  onChange?: (value: string) => void;
  required?: boolean;
  disabled?: boolean;
  className?: string;
  /** Marks the text input as a jump target for the scope bar. */
  dataField?: string;
}) {
  const [uncontrolled, setUncontrolled] = useState(defaultValue);
  const picker = useRef<HTMLInputElement>(null);
  const value = controlledValue ?? uncontrolled;
  const accessibleLabel = ariaLabel ?? label;

  function update(next: string) {
    if (controlledValue === undefined) setUncontrolled(next);
    onChange?.(next);
  }

  function openPicker() {
    const input = picker.current;
    if (!input) return;
    try {
      input.showPicker();
    } catch {
      input.focus();
    }
  }

  return (
    <label className={className}>
      <span>{label}</span>
      <span className="iso-date-field">
        <input
          type="text"
          name={name}
          aria-label={accessibleLabel}
          data-scope-field={dataField}
          inputMode="numeric"
          autoComplete="off"
          spellCheck={false}
          placeholder="YYYY-MM-DD"
          pattern="\d{4}-\d{2}-\d{2}"
          title="请使用 YYYY-MM-DD 格式"
          maxLength={10}
          value={value}
          required={required}
          disabled={disabled}
          onChange={(event) => update(event.target.value)}
          onBlur={() => {
            const normalized = normalize(value);
            if (normalized !== value) update(normalized);
          }}
        />
        <button
          type="button"
          className="iso-date-picker"
          aria-label={`打开${accessibleLabel}日历`}
          disabled={disabled}
          onClick={openPicker}
        >
          <Icon name="calendar" size={16} />
        </button>
        <input
          ref={picker}
          type="date"
          className="iso-date-native"
          tabIndex={-1}
          aria-hidden="true"
          value={ISO_DATE.test(value) ? value : ""}
          disabled={disabled}
          onChange={(event) => update(event.target.value)}
        />
      </span>
    </label>
  );
}
