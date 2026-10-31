import shape from "./generated/shape.js";
import { sha256 } from "@noble/hashes/sha2.js";
import { bytesToHex } from "@noble/hashes/utils.js";
import { contract } from "./generated/contract.js";
import type { InterventionCard } from "./generated/card.js";

export type Validation =
  | { ok: true; card: InterventionCard }
  | { ok: false; reason: string };
const encoder = new TextEncoder();
export const MAX_CARD_BYTES = 1048576;
const refused = new RegExp(contract.refused, "u");
const rules = contract.rules as Record<string, Record<string, string[]>>;
type Obj = Record<string, any>; // Internal schema traversal; public inputs remain unknown.
const defs = contract.schema.$defs as Record<string, Obj>;
const fail = (): never => {
  throw new Error("Card unavailable: invalid contract data");
};
const assert = (condition: unknown): void => {
  if (!condition) fail();
};
const has = (v: Obj, k: string): boolean => Object.hasOwn(v, k);

function date(value: string): void {
  const m =
    /^(\d{4})-(\d{2})-(\d{2})T([01]\d|2[0-3]):([0-5]\d):([0-5]\d)(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$/.exec(
      value,
    );
  assert(m);
  const [, y, mo, d] = m!;
  const year = Number(y),
    month = Number(mo),
    day = Number(d);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  assert(
    year > 0 &&
      month >= 1 &&
      month <= 12 &&
      day >= 1 &&
      day <=
        [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1],
  );
  const zone = m![8];
  // Python's fromisoformat permits offset-minute overflow but requires <24h total.
  assert(
    zone === "Z" ||
      Number(zone.slice(1, 3)) * 60 + Number(zone.slice(4)) < 1440,
  );
}
export function validDateTime(value: string): boolean {
  try {
    assert(value.length <= 40);
    date(value);
    return true;
  } catch {
    return false;
  }
}
function fieldRule(value: string, rule: string): void {
  if (rule === "_plain") {
    assert(!/[<>]/u.test(value) && !refused.test(value));
  } else if (rule === "_plain_long") {
    assert(!refused.test(value.replaceAll("\n", "")));
  } else if (rule === "_date_time") date(value);
  else if (rule === "_work_id")
    assert(
      !value.startsWith("wi_") ||
        /^wi_[0-7][0-9A-HJKMNP-TV-Z]{25}$/.test(value),
    );
  else if (rule === "_not_a_work_id")
    assert(!value.startsWith("wi_") && !value.startsWith("todo_"));
  else if (rule === "_class_l") {
    assert(encoder.encode(value).length <= 4096);
    for (const ch of value) {
      const cp = ch.codePointAt(0)!;
      assert(
        !(cp >= 0xd800 && cp <= 0xdfff) &&
          !contract.unassigned.some(([a, b]) => cp >= a && cp <= b),
      );
      assert(!(cp < 32 || (cp >= 127 && cp <= 159)) || cp === 10);
    }
  } else fail(); // Newly added Python rule must get an explicit browser implementation.
}
function semantic(name: string, v: Obj): void {
  if (has(v, "intervention_ref"))
    assert(v.work_ref && v.intervention_ref === v.work_ref.work_id);
  const actions = [v.primary_action, ...(v.secondary_actions ?? [])].filter(
    Boolean,
  );
  actions.forEach((a) => assert(a.enabled || has(a, "reason")));
  switch (name) {
    case "Action": {
      const names: Obj = {
        intervention: "type",
        reserved: "reserved_operation",
        task: "task_operation",
      };
      for (const [route, key] of Object.entries(names))
        assert(has(v, key) === (v.route === route));
      break;
    }
    case "IntegerAnswer":
      assert(
        v.minimum <= v.maximum &&
          v.presets.every((n: number) => n >= v.minimum && n <= v.maximum),
      );
      assert(
        new Set(v.presets).size === v.presets.length &&
          (v.presets.length || v.allow_other),
      );
      assert(
        !has(v, "recommended") ||
          (v.recommended >= v.minimum && v.recommended <= v.maximum),
      );
      break;
    case "ReviewImage":
      assert(v.width * v.height <= 16777216);
      break;
    case "PinnedText":
      assert(
        v.sha256 === "sha256:" + bytesToHex(sha256(encoder.encode(v.text))),
      );
      break;
    case "ResultSlot":
      assert(
        (v.value_type === "text") === has(v, "max_utf8_bytes") &&
          (!has(v, "max_data_rows") || v.value_type === "text"),
      );
      break;
    case "Decision": {
      const names: Obj = {
        decide: "answer",
        review: "artifact",
        do: "checklist",
        session: "session",
      };
      for (const [kind, key] of Object.entries(names))
        assert(has(v, key) === (v.ask_type === kind));
      assert(
        !has(v, "review_images") ||
          (v.ask_type === "review" &&
            v.review_images.reduce(
              (s: number, i: Obj) => s + i.byte_length,
              0,
            ) <= 16777216),
      );
      for (const key of [
        "result_slots",
        "pinned_texts",
        "result_max_total_bytes",
        "target_label",
      ])
        assert(!has(v, key) || v.ask_type === "do");
      assert(
        !has(v, "result_max_total_bytes") ||
          (v.result_slots &&
            v.result_max_total_bytes <=
              v.result_slots.reduce(
                (s: number, i: Obj) => s + (i.max_utf8_bytes ?? 0),
                0,
              )),
      );
      break;
    }
    case "RequestStatus":
      assert(!has(v, "overdue") || has(v, "deadline"));
      break;
    case "Outcome":
      assert(
        v.outcome === "verified"
          ? v.completion_evidence?.length && v.verification_ref
          : !v.completion_evidence?.length && !v.verification_ref,
      );
      break;
    case "SeatAnswer":
      assert(v.answer_state !== "answered" || (v.answer_at && v.answer_ref));
      assert(
        v.answer_state !== "not_answered" ||
          !["answer_at", "answer_age_h", "answer_ref"].some((k) => has(v, k)),
      );
      break;
    case "Milestone":
      assert(v.stage === "unknown" || (v.what && v.when && v.evidence_ref));
      break;
    case "NextAct":
      assert((v.time_quality === "unknown") === !has(v, "time"));
      break;
    case "ProjectCheckpoint":
      assert(
        !has(v, "project_checkpoint_age_h") ||
          v.seats.every(
            (s: Obj) =>
              (s.answer_state === "answered" && has(s, "answer_age_h")) ||
              (s.latest_checkpoint && has(s.latest_checkpoint, "age_h")),
          ),
      );
      if (v.last_product && v.last_administrative)
        assert(
          ["what", "when", "evidence_ref", "stage"].some(
            (k) => v.last_product[k] !== v.last_administrative[k],
          ),
        );
      assert(
        actions.every(
          (a) =>
            a.route === "intervention" &&
            [
              "request_checkpoint",
              "request_unblock",
              "explain_blocker",
            ].includes(a.type),
        ),
      );
      break;
    case "ChoiceReply":
      assert(
        new Set(v.options.map((o: Obj) => o.id)).size === v.options.length,
      );
      break;
    case "Message":
      assert(!v.reply || v.reply.answers_digest === v.source_digest);
      assert(!has(v, "in_reply_to") || v.in_reply_to !== v.message_id);
      break;
    case "Approval":
      assert((v.status === "pending") === !has(v, "decided_at"));
      break;
  }
}
function walk(
  schema: Obj,
  v: any,
  path = "",
  floats = new Set<string>(),
): void {
  if (schema.type === "integer") assert(!floats.has(path));
  if (schema.$ref) {
    const name = schema.$ref.split("/").at(-1)!;
    const node = defs[name];
    for (const [key, rs] of Object.entries(rules[name] ?? {}))
      if (has(v, key)) for (const r of rs) fieldRule(v[key], r);
    semantic(name, v);
    walk(node, v, path, floats);
    return;
  }
  if (schema.oneOf || schema.anyOf) {
    const branches = schema.oneOf ?? schema.anyOf;
    const match = branches.find((b: Obj) => {
      const n = b.$ref ? defs[b.$ref.split("/").at(-1)!] : b;
      const constants = Object.entries(n.properties ?? {}).filter(([, p]) =>
        has(p as Obj, "const"),
      );
      return constants.length
        ? constants.every(([k, p]) => v?.[k] === (p as Obj).const)
        : n.type === "array"
          ? Array.isArray(v)
          : typeof v === n.type;
    });
    assert(match);
    walk(match, v, path, floats);
    return;
  }
  if (schema.properties)
    for (const [key, node] of Object.entries(schema.properties))
      if (has(v, key)) walk(node as Obj, v[key], path + "/" + key, floats);
  if (schema.items)
    v.forEach((item: unknown, i: number) =>
      walk(schema.items, item, path + "/" + i, floats),
    );
}

// Validate plain JSON objects only, without invoking accessors or serialisation hooks.
function snapshot(value: unknown, depth = 0, seen = new Set<object>()): any {
  assert(depth <= 32);
  if (value === null || typeof value === "boolean" || typeof value === "string")
    return value;
  if (typeof value === "number") {
    assert(Number.isFinite(value));
    return value;
  }
  assert(typeof value === "object" && value !== null);
  const obj = value as object;
  assert(!seen.has(obj));
  seen.add(obj);
  assert(
    Array.isArray(obj) ||
      Object.getPrototypeOf(obj) === Object.prototype ||
      Object.getPrototypeOf(obj) === null,
  );
  const out: any = Array.isArray(obj) ? [] : Object.create(null);
  for (const key of Reflect.ownKeys(obj)) {
    if (Array.isArray(obj) && key === "length") continue;
    assert(typeof key === "string");
    const d = Object.getOwnPropertyDescriptor(obj, key)!;
    assert("value" in d && d.enumerable);
    out[key] = snapshot(d.value, depth + 1, seen);
  }
  seen.delete(obj);
  return out;
}
function freeze<T>(v: T): T {
  if (v && typeof v === "object") {
    Object.values(v).forEach(freeze);
    Object.freeze(v);
  }
  return v;
}
export function validateCard(value: unknown): Validation {
  try {
    const card = snapshot(value);
    assert(encoder.encode(JSON.stringify(card)).length <= MAX_CARD_BYTES);
    assert(shape(card));
    walk(contract.schema, card);
    return { ok: true, card: freeze(card) as InterventionCard };
  } catch {
    return { ok: false, reason: "Card unavailable: invalid contract data" };
  }
}

/** Strict byte ingress. Duplicate keys are refused before JSON.parse loses them. */
export function parseCard(input: string | Uint8Array): Validation {
  try {
    assert(
      typeof input === "string"
        ? encoder.encode(input).length <= MAX_CARD_BYTES
        : input.byteLength <= MAX_CARD_BYTES,
    );
    const text =
      typeof input === "string"
        ? input
        : new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(
            input,
          );
    let i = 0;
    const floats = new Set<string>();
    const ws = () => {
      while (/[\x20\t\r\n]/.test(text[i] ?? "!")) i++;
    };
    const str = (): string => {
      const start = i++;
      while (i < text.length) {
        if (text[i] === "\\") {
          i += 2;
          continue;
        }
        if (text[i++] === '"') return JSON.parse(text.slice(start, i));
      }
      return fail();
    };
    const read = (depth: number, path: string): void => {
      assert(depth <= 32);
      ws();
      if (text[i] === '"') {
        str();
        return;
      }
      const c = text[i++];
      if (c === "{" || c === "[") {
        const end = c === "{" ? "}" : "]",
          keys = new Set<string>();
        ws();
        if (text[i] === end) {
          i++;
          return;
        }
        let index = 0;
        while (i < text.length) {
          let key = String(index++);
          ws();
          if (c === "{") {
            assert(text[i] === '"');
            const k = str();
            key = k;
            assert(!keys.has(k));
            keys.add(k);
            ws();
            assert(text[i++] === ":");
          }
          read(depth + 1, path + "/" + key);
          ws();
          if (text[i] === end) {
            i++;
            return;
          }
          assert(text[i++] === ",");
        }
        fail();
      }
      i--;
      const m =
        /^(?:true|false|null|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)/.exec(
          text.slice(i),
        );
      assert(m);
      if (/^-?\d/.test(m![0]) && /[.eE]/.test(m![0])) floats.add(path);
      i += m![0].length;
    };
    read(0, "");
    ws();
    assert(i === text.length);
    const result = validateCard(JSON.parse(text));
    if (result.ok) walk(contract.schema, result.card, "", floats);
    return result;
  } catch {
    return { ok: false, reason: "Card unavailable: invalid JSON" };
  }
}
