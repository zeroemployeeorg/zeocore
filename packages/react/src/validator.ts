import shape from "./generated/shape.js";
import { sha256 } from "@noble/hashes/sha2.js";
import { bytesToHex } from "@noble/hashes/utils.js";
import { contract } from "./generated/contract.js";
import type { InterventionCard } from "./generated/card.js";
import {
  assert,
  has,
  makeValidator,
  validDateTime,
  type Contract,
  type Obj,
} from "./core.js";

export { validDateTime };
export type Validation =
  | { ok: true; card: InterventionCard }
  | { ok: false; reason: string };
const encoder = new TextEncoder();
export const MAX_CARD_BYTES = 1048576;

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
    case "SourceItem":
      assert(
        !has(v, "copy_line") ||
          !has(v, "commands") ||
          v.copy_line === v.commands[0],
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

const checker = makeValidator<InterventionCard>({
  contract: contract as unknown as Contract,
  shape,
  semantic,
  maxBytes: MAX_CARD_BYTES,
  noun: "Card",
});
const asCard = (r: ReturnType<typeof checker.validate>): Validation =>
  r.ok ? { ok: true, card: r.value } : r;
export function validateCard(value: unknown): Validation {
  return asCard(checker.validate(value));
}
/** Strict byte ingress. Duplicate keys are refused before JSON.parse loses them. */
export function parseCard(input: string | Uint8Array): Validation {
  return asCard(checker.parse(input));
}
