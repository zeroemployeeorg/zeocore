// The checks every contract shares: closed JSON, the generated shape, the field rules and the
// byte-exact parser. A contract adds only its own cross-field rules (`semantic`).
export type Obj = Record<string, any>; // Internal schema traversal; public inputs remain unknown.
export interface Contract {
  schema: Obj & { $defs: Record<string, Obj> };
  rules: Record<string, Record<string, string[]>>;
  unassigned: [number, number][];
  refused: string;
}
export interface Spec {
  contract: Contract;
  /** The build-generated Ajv validator for this contract's schema. */
  shape: (value: unknown) => boolean;
  /** Cross-field rules of one named definition; it asserts, it never returns a verdict. */
  semantic: (name: string, v: Obj) => void;
  maxBytes: number;
  /** "Card" or "Calendar": the first word of the refusal text. */
  noun: string;
}
export type Checked<T> = { ok: true; value: T } | { ok: false; reason: string };

const encoder = new TextEncoder();
export const fail = (): never => {
  throw new Error("unavailable: invalid contract data");
};
export const assert = (condition: unknown): void => {
  if (!condition) fail();
};
export const has = (v: Obj, k: string): boolean => Object.hasOwn(v, k);

const DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
const leapYear = (y: number) => y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0);
const monthDays = (y: number, m: number) =>
  m === 2 && leapYear(y) ? 29 : DAYS[m - 1];
/** Days since 0001-01-01 of a real proleptic Gregorian date. */
export function dayNumber(y: number, m: number, d: number): number {
  const before = y - 1;
  let n =
    before * 365 +
    Math.floor(before / 4) -
    Math.floor(before / 100) +
    Math.floor(before / 400);
  for (let i = 1; i < m; i++) n += monthDays(y, i);
  return n + d - 1;
}

const DATE_TIME =
  /^(\d{4})-(\d{2})-(\d{2})T([01]\d|2[0-3]):([0-5]\d):([0-5]\d)(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$/;
function parts(value: string) {
  const m = DATE_TIME.exec(value);
  assert(m);
  const [, y, mo, d, h, mi, s, frac, zone] = m!;
  const year = Number(y),
    month = Number(mo),
    day = Number(d);
  assert(
    year > 0 &&
      month >= 1 &&
      month <= 12 &&
      day >= 1 &&
      day <= monthDays(year, month),
  );
  // Python's fromisoformat permits offset-minute overflow but requires <24h total.
  const offset =
    zone === "Z"
      ? 0
      : (zone[0] === "-" ? -1 : 1) *
        (Number(zone.slice(1, 3)) * 60 + Number(zone.slice(4)));
  assert(Math.abs(offset) < 1440);
  return {
    year,
    month,
    day,
    h: Number(h),
    mi: Number(mi),
    s: Number(s),
    micro: frac ? Number(frac.slice(1).padEnd(6, "0")) : 0,
    offset,
  };
}
function date(value: string): void {
  parts(value);
}
/** The instant in microseconds since 0001-01-01 UTC: what Python compares for aware datetimes. */
export function instantMicros(value: string): bigint {
  const p = parts(value);
  const seconds =
    BigInt(dayNumber(p.year, p.month, p.day)) * 86400n +
    BigInt(p.h * 3600 + p.mi * 60 + p.s - p.offset * 60);
  return seconds * 1000000n + BigInt(p.micro);
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
/** A calendar date, written YYYY-MM-DD, that exists. */
export function localDay(value: string): number {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  assert(m);
  const year = Number(m![1]),
    month = Number(m![2]),
    day = Number(m![3]);
  assert(year > 0 && month >= 1 && month <= 12 && day >= 1);
  assert(day <= monthDays(year, month));
  return dayNumber(year, month, day);
}

export function makeValidator<T>(spec: Spec) {
  const { contract } = spec;
  const refused = new RegExp(contract.refused, "u");
  const rules = contract.rules;
  const defs = contract.schema.$defs;
  const unavailable = `${spec.noun} unavailable: invalid contract data`;
  const invalidJson = `${spec.noun} unavailable: invalid JSON`;

  function fieldRule(value: string, rule: string): void {
    if (rule === "_plain") {
      assert(!/[<>]/u.test(value) && !refused.test(value));
    } else if (rule === "_plain_long") {
      assert(!refused.test(value.replaceAll("\n", "")));
    } else if (rule === "_date_time") date(value);
    else if (rule === "_local_date") localDay(value);
    else if (rule === "_work_id")
      assert(
        !value.startsWith("wi_") ||
          /^wi_[0-7][0-9A-HJKMNP-TV-Z]{25}$/.test(value),
      );
    else if (rule === "_not_a_work_id")
      assert(!value.startsWith("wi_") && !value.startsWith("todo_"));
    else if (rule === "_class_l" || rule === "_copy_text") {
      assert(
        encoder.encode(value).length <= (rule === "_copy_text" ? 8192 : 4096),
      );
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

  // A rule on a list field is the rule of each entry, as in the Python models.
  function fieldRules(v: Obj, key: string, rs: string[]): void {
    for (const item of Array.isArray(v[key]) ? v[key] : [v[key]])
      for (const r of rs) fieldRule(item, r);
  }

  function walk(
    schema: Obj,
    v: any,
    path = "",
    floats = new Set<string>(),
  ): void {
    if (schema.type === "integer") {
      assert(!floats.has(path));
      assert(Number.isSafeInteger(v));
    }
    if (schema.$ref) {
      const name = schema.$ref.split("/").at(-1)!;
      const node = defs[name];
      for (const [key, rs] of Object.entries(rules[name] ?? {}))
        if (has(v, key)) fieldRules(v, key, rs);
      spec.semantic(name, v);
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

  // A schema whose root is the model itself (not a reference to it) names its own rules by title.
  function root(doc: any): void {
    const top = contract.schema;
    if (top.$ref || top.oneOf || top.anyOf) return;
    for (const [key, rs] of Object.entries(rules[top.title] ?? {}))
      if (has(doc, key)) fieldRules(doc, key, rs);
    spec.semantic(top.title, doc);
  }

  const LONE_SURROGATE = /\p{Surrogate}/u;

  // Validate plain JSON objects only, without invoking accessors or serialisation hooks.
  function snapshot(value: unknown, depth = 0, seen = new Set<object>()): any {
    assert(depth <= 32);
    if (typeof value === "string") {
      assert(!LONE_SURROGATE.test(value));
      return value;
    }
    if (value === null || typeof value === "boolean") return value;
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
      assert(typeof key === "string" && !LONE_SURROGATE.test(key));
      const d = Object.getOwnPropertyDescriptor(obj, key)!;
      assert("value" in d && d.enumerable);
      out[key] = snapshot(d.value, depth + 1, seen);
    }
    seen.delete(obj);
    return out;
  }
  function freeze<U>(v: U): U {
    if (v && typeof v === "object") {
      Object.values(v).forEach(freeze);
      Object.freeze(v);
    }
    return v;
  }

  function validate(value: unknown): Checked<T> {
    try {
      const doc = snapshot(value);
      assert(encoder.encode(JSON.stringify(doc)).length <= spec.maxBytes);
      assert(spec.shape(doc));
      walk(contract.schema, doc);
      root(doc);
      return { ok: true, value: freeze(doc) as T };
    } catch {
      return { ok: false, reason: unavailable };
    }
  }

  /** Strict byte ingress. Duplicate keys are refused before JSON.parse loses them. */
  function parse(input: string | Uint8Array): Checked<T> {
    try {
      assert(
        typeof input === "string"
          ? encoder.encode(input).length <= spec.maxBytes
          : input.byteLength <= spec.maxBytes,
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
      const result = validate(JSON.parse(text));
      if (result.ok) walk(contract.schema, result.value, "", floats);
      return result;
    } catch {
      return { ok: false, reason: invalidJson };
    }
  }

  return { validate, parse };
}
