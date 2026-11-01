import { useId } from "react";
import { validDateTime } from "./validator.js";
import type { Decision } from "./generated/card.js";
export type ResultValues = Record<string, string | boolean>;
export function validResults(card: Decision, values: ResultValues): boolean {
  let total = 0;
  for (const slot of card.result_slots ?? []) {
    const value = values[slot.slot_id];
    if (value === undefined || value === "") {
      if (slot.required !== false) return false;
      continue;
    }
    if (slot.value_type === "confirm") {
      if (typeof value !== "boolean") return false;
    } else {
      if (typeof value !== "string") return false;
      if (slot.value_type === "timestamp") {
        if (!validDateTime(value)) return false;
      } else {
        const bytes = new TextEncoder().encode(value).length;
        total += bytes;
        if (
          bytes > slot.max_utf8_bytes! ||
          (slot.max_data_rows !== undefined &&
            value.split("\n").length > slot.max_data_rows)
        )
          return false;
      }
    }
  }
  return (
    card.result_max_total_bytes === undefined ||
    total <= card.result_max_total_bytes
  );
}
export function Results({
  card,
  values,
  onChange,
  enabled,
}: {
  card: Decision;
  values: ResultValues;
  onChange: (v: ResultValues) => void;
  enabled: boolean;
}) {
  const prefix = useId();
  return (
    <fieldset disabled={!enabled}>
      <legend>Task results</legend>
      {card.result_slots?.map((slot) => {
        const id = prefix + slot.slot_id,
          value = values[slot.slot_id];
        return (
          <div key={slot.slot_id}>
            <label htmlFor={id}>
              {slot.label}
              {slot.required !== false ? " (required)" : ""}
            </label>
            {slot.value_type === "confirm" ? (
              <select
                id={id}
                value={value === undefined ? "" : String(value)}
                onChange={(e) =>
                  onChange({
                    ...values,
                    [slot.slot_id]: e.target.value === "true",
                  })
                }
              >
                <option value="" disabled>
                  Select an answer
                </option>
                <option value="true">Yes</option>
                <option value="false">No</option>
              </select>
            ) : (
              <textarea
                id={id}
                value={typeof value === "string" ? value : ""}
                placeholder={
                  slot.value_type === "timestamp"
                    ? "2026-10-31T10:00:00Z"
                    : undefined
                }
                onChange={(e) =>
                  onChange({ ...values, [slot.slot_id]: e.target.value })
                }
              />
            )}
            {slot.max_utf8_bytes !== undefined && (
              <small>
                Maximum {slot.max_utf8_bytes} UTF-8 bytes
                {slot.max_data_rows !== undefined
                  ? `, ${slot.max_data_rows} rows`
                  : ""}
              </small>
            )}
          </div>
        );
      })}
      <small>
        Results are submitted to the owning service for verification; entering
        them does not complete the task.
      </small>
    </fieldset>
  );
}
