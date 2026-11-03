import { useId, useState, type ReactNode } from "react";
import { validateCard } from "./validator.js";
import type {
  InterventionCard,
  Message,
  Decision,
  Action,
} from "./generated/card.js";
import { Results, validResults, type ResultValues } from "./results.js";
export type * from "./generated/card.js";
export { validateCard, parseCard } from "./validator.js";
export {
  CommitmentCalendar,
  type CommitmentCalendarProps,
} from "./calendar.js";
export { validateCalendar, parseCalendar } from "./calendar-validator.js";
export type {
  CalendarView as CalendarViewPayload,
  CommitmentItem,
} from "./generated/calendar.js";

/** Local intent only. The host must authenticate, re-read and authorize before any effect. */
export type CardIntent =
  | {
      kind: "action";
      action: Action;
      card: InterventionCard;
      answer?: string | number | boolean;
      results?: ResultValues;
    }
  | {
      kind: "reply";
      reply_ref: string;
      answers_digest: string;
      text?: string;
      option_id?: string;
    };
export interface CardProps {
  value: unknown;
  onIntent?: (intent: CardIntent) => void;
  /** Host-owned current authority/freshness decision; absent means controls disabled. */
  interaction?: { enabled: boolean; reason?: string };
}
const label = (s: string) => s.replaceAll("_", " ");
function Time({ value }: { value?: string }) {
  return value ? <time dateTime={value}>{value}</time> : <>Unknown</>;
}
function Row({ name, children }: { name: string; children: ReactNode }) {
  return (
    <div className="zc-row">
      <dt>{name}</dt>
      <dd>{children}</dd>
    </div>
  );
}
function Freshness({ card }: { card: InterventionCard }) {
  return (
    <details className="zc-freshness">
      <summary>Source freshness</summary>
      {"freshness" in card && card.freshness?.length ? (
        card.freshness.map((s, i) => (
          <dl key={i}>
            <Row name={s.source}>{s.state}</Row>
            <Row name="Observed">
              <Time value={s.observed_at} />
            </Row>
            <Row name="Collected">
              <Time value={s.collected_at} />
            </Row>
          </dl>
        ))
      ) : (
        <p>Source freshness unknown</p>
      )}
      {"sources" in card &&
        card.sources.map((s, i) => (
          <dl key={i}>
            <Row name={s.source}>{s.state}</Row>
            <Row name="Observed">
              <Time value={s.observed_at} />
            </Row>
            <Row name="Collected">
              <Time value={s.collected_at} />
            </Row>
            {s.reason && <Row name="Reason">{s.reason}</Row>}
          </dl>
        ))}
    </details>
  );
}
function Evidence({ card }: { card: InterventionCard }) {
  return "evidence" in card && card.evidence.length > 0 ? (
    <details>
      <summary>Evidence ({card.evidence.length})</summary>
      <ul>
        {card.evidence.map((e, i) => (
          <li key={i}>
            {e.kind}: <code>{e.ref}</code>
          </li>
        ))}
      </ul>
    </details>
  ) : null;
}
function Reply({
  card,
  onIntent,
  enabled,
}: {
  card: Message;
  onIntent?: CardProps["onIntent"];
  enabled: boolean;
}) {
  const id = useId(),
    [text, setText] = useState(""),
    [choice, setChoice] = useState("");
  const reply = card.reply;
  if (!reply) return null;
  const valid =
    reply.mode === "text"
      ? text.length > 0 && [...text].length <= reply.max_length
      : reply.options.some((o) => o.id === choice);
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (!enabled || !valid) return;
        onIntent?.({
          kind: "reply",
          reply_ref: reply.reply_ref,
          answers_digest: reply.answers_digest,
          ...(reply.mode === "text" ? { text } : { option_id: choice }),
        });
      }}
    >
      {reply.closes_at && (
        <p>
          Reply closes <Time value={reply.closes_at} />
        </p>
      )}
      {reply.mode === "text" ? (
        <>
          <label htmlFor={id}>Your reply</label>
          <textarea
            id={id}
            value={text}
            onChange={(e) => setText(e.target.value)}
            disabled={!enabled}
            aria-describedby={id + "-count"}
          />
          <small id={id + "-count"}>
            {[...text].length} / {reply.max_length}
          </small>
        </>
      ) : (
        <fieldset disabled={!enabled}>
          <legend>Your answer</legend>
          {reply.options.map((o) => (
            <label className="zc-option" key={o.id}>
              <input
                type="radio"
                name={id}
                value={o.id}
                checked={choice === o.id}
                onChange={() => setChoice(o.id)}
              />
              {o.label}
            </label>
          ))}
        </fieldset>
      )}
      <button type="submit" disabled={!enabled || !valid}>
        Use this reply
      </button>
    </form>
  );
}
function DecisionBody({
  card,
  answer,
  setAnswer,
  enabled,
}: {
  card: Decision;
  answer: string | number | boolean | undefined;
  setAnswer: (v: string | number | boolean) => void;
  enabled: boolean;
}) {
  const id = useId();
  return (
    <>
      <h3>{card.question}</h3>
      <p className="zc-muted">Needs your {label(card.why_operator)}</p>
      {card.answer?.kind === "choice" && (
        <fieldset disabled={!enabled}>
          <legend>Choose an answer</legend>
          {card.answer.options.map((o) => (
            <label className="zc-option" key={o.id}>
              <input
                type="radio"
                name={id}
                checked={answer === o.id}
                onChange={() => setAnswer(o.id)}
              />
              {o.label}
            </label>
          ))}
        </fieldset>
      )}
      {card.answer?.kind === "bounded_integer" && (
        <fieldset disabled={!enabled}>
          <legend>
            {label(card.answer.unit)} · {card.answer.minimum}–
            {card.answer.maximum}
          </legend>
          <div className="zc-presets">
            {card.answer.presets.map((n) => (
              <button
                type="button"
                aria-pressed={answer === n}
                onClick={() => setAnswer(n)}
                key={n}
              >
                {n}
              </button>
            ))}
          </div>
          {card.answer.allow_other && (
            <>
              <label htmlFor={id}>Other {card.answer.unit}</label>
              <input
                id={id}
                type="number"
                min={card.answer.minimum}
                max={card.answer.maximum}
                step={1}
                value={typeof answer === "number" ? answer : ""}
                onChange={(e) =>
                  setAnswer(e.target.value === "" ? "" : Number(e.target.value))
                }
              />
            </>
          )}
          {card.answer.recommended !== undefined && (
            <small>
              Suggested: {card.answer.recommended}. No answer selected
              automatically.
            </small>
          )}
        </fieldset>
      )}
      {card.artifact && (
        <dl>
          <Row name="Artifact">{card.artifact.ref}</Row>
          <Row name="Revision">
            <code>{card.artifact.revision}</code>
          </Row>
        </dl>
      )}
      {card.review_images && (
        <ul>
          {card.review_images.map((i) => (
            <li key={i.media_ref}>
              {i.label}: {i.alt}
              <p className="zc-muted">
                Authenticated media viewer required · {i.media_ref}
              </p>
            </li>
          ))}
        </ul>
      )}
      {card.checklist && (
        <ul className="zc-checklist">
          {card.checklist.map((s, i) => (
            <li key={i}>
              {s.done ? "✓" : "○"} {s.text}
            </li>
          ))}
        </ul>
      )}
      {card.target_label && <p>Target: {card.target_label}</p>}
      {card.pinned_texts?.map((t, i) => (
        <details key={i}>
          <summary>{t.label} · display only</summary>
          <pre>{t.text}</pre>
          <code>{t.sha256}</code>
        </details>
      ))}
      {card.result_slots && (
        <p className="zc-muted">
          This task requires structured results. Provide{" "}
          {card.result_slots.map((s) => s.label).join(", ")}.
        </p>
      )}
      {card.session && (
        <p>
          <Time value={card.session.start} /> · {card.session.duration_minutes}{" "}
          minutes
        </p>
      )}
    </>
  );
}
function validAnswer(card: InterventionCard, answer: unknown): boolean {
  if (card.situation !== "decision" || card.ask_type !== "decide") return true;
  const a = card.answer!;
  return a.kind === "choice"
    ? a.options.some((o) => o.id === answer)
    : typeof answer === "number" &&
        Number.isInteger(answer) &&
        answer >= a.minimum &&
        answer <= a.maximum &&
        (a.allow_other || a.presets.includes(answer));
}
function Content({
  card,
  props,
  answer,
  setAnswer,
}: {
  card: InterventionCard;
  props: CardProps;
  answer: string | number | boolean | undefined;
  setAnswer: (v: string | number | boolean) => void;
}) {
  const enabled = Boolean(props.onIntent && props.interaction?.enabled);
  switch (card.situation) {
    case "message":
      return (
        <>
          <p className="zc-muted">
            {card.from} · <Time value={card.sent_at} />
          </p>
          <h3>{card.subject}</h3>
          <p className="zc-body">{card.body}</p>
          {card.links?.map((l, i) => (
            <a
              key={i}
              href={l.url}
              target="_blank"
              rel="noopener noreferrer"
              referrerPolicy="no-referrer"
            >
              {l.label} ↗
            </a>
          ))}
          {card.in_reply_to && <p>In reply to {card.in_reply_to}</p>}
          {card.expires_at && (
            <p>
              Expires <Time value={card.expires_at} />
            </p>
          )}
          <Reply card={card} onIntent={props.onIntent} enabled={enabled} />
        </>
      );
    case "approval":
      return (
        <>
          <h3>{card.operation_id}</h3>
          <p className="zc-body">{card.preview}</p>
          <p className="zc-badge">{card.status}</p>
          <dl>
            <Row name="Issued">
              <Time value={card.issued_at} />
            </Row>
            <Row name="Expires">
              <Time value={card.expires_at} />
            </Row>
            {card.decided_at && (
              <Row name="Decided">
                <Time value={card.decided_at} />
              </Row>
            )}
            <Row name="Request">
              <code>{card.request_digest}</code>
            </Row>
          </dl>
          <p className="zc-muted">
            Display only. Decisions belong to the service that owns them.
          </p>
        </>
      );
    case "work_location":
      return (
        <>
          <h3>{card.claimed_work}</h3>
          <p>{label(card.location_state)}</p>
          <ul>
            {card.artifact_refs.map((a, i) => (
              <li key={i}>
                {a.kind}: <code>{a.ref}</code>
              </li>
            ))}
          </ul>
        </>
      );
    case "checkpoint":
      return (
        <>
          <h3>Checkpoint requested</h3>
          <dl>
            <Row name="Requested">
              <Time value={card.requested_at} />
            </Row>
            <Row name="Due">
              <Time value={card.due_at} />
            </Row>
            <Row name="Delivery">{card.delivery}</Row>
            <Row name="Response">{label(card.response)}</Row>
          </dl>
        </>
      );
    case "blocker":
      return (
        <>
          <h3>{card.reported_blocker}</h3>
          <p>
            Since <Time value={card.since} />
          </p>
          {card.depends_on && <p>Waiting on {card.depends_on}</p>}
          {card.release_condition && (
            <p>Unblocked when: {card.release_condition}</p>
          )}
        </>
      );
    case "decision":
      return (
        <DecisionBody
          card={card}
          answer={answer}
          setAnswer={setAnswer}
          enabled={enabled}
        />
      );
    case "request_status":
      return (
        <>
          <h3>{card.summary}</h3>
          <dl>
            <Row name="Delivery">{card.delivery}</Row>
            <Row name="Response">{label(card.response)}</Row>
            <Row name="Outcome">{card.outcome}</Row>
            <Row name="Deadline">
              <Time value={card.deadline} />
            </Row>
            <Row name="Overdue">
              {card.overdue === undefined
                ? "Unknown"
                : card.overdue
                  ? "Yes"
                  : "No"}
            </Row>
          </dl>
        </>
      );
    case "outcome":
      return (
        <>
          <h3>{label(card.outcome)}</h3>
          {card.reason && <p>{card.reason}</p>}
          <p>
            Closed <Time value={card.closed_at} />
          </p>
          {card.verification_ref && (
            <p>Verification: {card.verification_ref}</p>
          )}
          {card.completion_evidence?.map((e, i) => (
            <p key={i}>
              {e.kind}: {e.ref}
            </p>
          ))}
        </>
      );
    case "source_item":
      return (
        <>
          <p className="zc-muted">
            {card.project} · {card.label}
          </p>
          <h3>{card.title}</h3>
          <p className="zc-badge">{card.state}</p>
          {card.next_action && <p className="zc-body">{card.next_action}</p>}
          {card.owner && <p>{card.owner}</p>}
          <p>
            Observed <Time value={card.observed_at} />
          </p>
          {card.deadline && (
            <p>
              Due <Time value={card.deadline} />
            </p>
          )}
          {card.commands ? (
            <ol className="zc-commands">
              {card.commands.map((command, i) => (
                <li key={i}>
                  <pre>{command}</pre>
                </li>
              ))}
            </ol>
          ) : (
            card.copy_line && <pre>{card.copy_line}</pre>
          )}
          {card.source_url && (
            <a
              href={card.source_url}
              target="_blank"
              rel="noopener noreferrer"
              referrerPolicy="no-referrer"
            >
              Open source ↗
            </a>
          )}
        </>
      );
    case "project_checkpoint":
      return (
        <>
          <h3>{card.project_ref}</h3>
          <p>
            Blocked: {card.flags.blocked} · Stale: {card.flags.stale}
          </p>
          {card.flags.reason && <p>{card.flags.reason}</p>}
          <ul>
            {card.seats.map((s, i) => (
              <li key={i}>
                <strong>{s.seat}</strong> · {label(s.answer_state)}
                {s.answer_at && (
                  <p>
                    Answered <Time value={s.answer_at} />
                  </p>
                )}
                {s.latest_checkpoint && (
                  <p>
                    Last known checkpoint:{" "}
                    <Time value={s.latest_checkpoint.answered_at} />
                  </p>
                )}
              </li>
            ))}
          </ul>
          {[
            ["Product", card.last_product],
            ["Administrative", card.last_administrative],
          ].map(([name, m]) =>
            typeof m === "object" && m ? (
              <dl key={String(name)}>
                <Row name={String(name)}>
                  {m.what ?? "Unknown"} · {m.stage}
                </Row>
                <Row name="When">
                  <Time value={m.when} />
                </Row>
                {m.evidence_ref && (
                  <Row name="Evidence">
                    <a
                      href={m.evidence_ref}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      Open evidence
                    </a>
                  </Row>
                )}
              </dl>
            ) : null,
          )}
          {card.wait && (
            <p>
              Waiting: {card.wait.text ?? "Unknown"} ·{" "}
              {card.wait.owner ?? "Unknown owner"}
            </p>
          )}
          {card.next_act && (
            <p>
              Next: {card.next_act.action} · {card.next_act.owner} ·{" "}
              {card.next_act.time_quality} <Time value={card.next_act.time} />
            </p>
          )}
        </>
      );
  }
}
function ValidCard({
  card,
  props,
}: {
  card: InterventionCard;
  props: CardProps;
}) {
  const [answer, setAnswer] = useState<string | number | boolean>();
  const [results, setResults] = useState<ResultValues>({});
  const actions =
    "secondary_actions" in card
      ? [card.primary_action, ...(card.secondary_actions ?? [])].filter(
          (a): a is Action => Boolean(a),
        )
      : [];
  const enabled = Boolean(props.onIntent && props.interaction?.enabled);
  return (
    <article className="zc-card" aria-label={label(card.situation)}>
      <header>
        <span className="zc-eyebrow">{label(card.situation)}</span>
        {"subject_ref" in card && (
          <span className="zc-muted">{card.subject_ref.project_id}</span>
        )}
      </header>
      <Content
        card={card}
        props={props}
        answer={answer}
        setAnswer={setAnswer}
      />
      {card.situation === "decision" && card.result_slots && (
        <Results
          card={card}
          values={results}
          onChange={setResults}
          enabled={enabled}
        />
      )}
      <Evidence card={card} />
      <Freshness card={card} />
      {(actions.length > 0 || (card.situation === "message" && card.reply)) &&
        !enabled && (
          <p className="zc-muted">
            {props.interaction?.reason ??
              "Interaction unavailable until the host revalidates this request."}
          </p>
        )}
      {actions.length > 0 && (
        <footer>
          {actions.map((a) => (
            <span key={a.action_id}>
              <button
                type="button"
                disabled={
                  !enabled ||
                  !a.enabled ||
                  !validAnswer(card, answer) ||
                  (card.situation === "decision" &&
                    !validResults(card, results))
                }
                onClick={() =>
                  props.onIntent?.({
                    kind: "action",
                    action: a,
                    card,
                    ...(answer === undefined ? {} : { answer }),
                    ...(card.situation === "decision" && card.result_slots
                      ? { results }
                      : {}),
                  })
                }
              >
                {a.label}
              </button>
              {!a.enabled && <small>{a.reason}</small>}
            </span>
          ))}
        </footer>
      )}
    </article>
  );
}
/** Validates before rendering. Changed payloads reset all draft answers. */
export function Card(props: CardProps) {
  const result = validateCard(props.value);
  if (!result.ok)
    return (
      <article className="zc-card zc-unavailable" role="status">
        <h3>Unavailable</h3>
        <p>This card could not be validated.</p>
      </article>
    );
  return (
    <ValidCard
      key={JSON.stringify(result.card)}
      card={result.card}
      props={props}
    />
  );
}
export const supportedSituations = Object.freeze([
  "work_location",
  "checkpoint",
  "blocker",
  "decision",
  "request_status",
  "outcome",
  "project_checkpoint",
  "source_item",
  "message",
  "approval",
] as const);
