import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Card,
  CommitmentCalendar,
  supportedSituations,
  validateCalendar,
  type CardIntent,
} from "../src/index";
import { validateCard } from "../src/validator";
import "../src/styles.css";
import "./style.css";
const files = import.meta.glob(
  "../../../tests/test_contracts/fixtures/intervention_cards/{valid,negative}/*.json",
  { eager: true, import: "default" },
) as Record<string, any>;
const fixtures = Object.entries(files)
  .filter(([name]) => !name.endsWith(".upsert.json"))
  .map(([name, value]) => ({
    name: name.split("/").at(-1)!,
    value: value.payload,
    negative: name.includes("/negative/"),
    expected: value.expect ?? "accepted",
  }));
const calendarFiles = import.meta.glob(
  "../../../tests/test_contracts/fixtures/calendar_view/{valid,negative}/*.json",
  { eager: true, import: "default" },
) as Record<string, any>;
const calendars = Object.entries(calendarFiles)
  .filter(([name]) => !name.endsWith(".upsert.json"))
  .map(([name, value]) => ({
    name: name.split("/").at(-1)!,
    value: value.payload,
    negative: name.includes("/negative/"),
  }));
function Gallery() {
  const [filter, setFilter] = useState("all"),
    [negative, setNegative] = useState(false),
    [intent, setIntent] = useState<CardIntent>(),
    [demo, setDemo] = useState(false);
  const calendar = filter === "calendar_view";
  const rows = calendar
    ? []
    : fixtures.filter(
        (f) =>
          f.negative === negative &&
          (filter === "all" || f.value.situation === filter),
      );
  const views = calendar
    ? calendars.filter((f) => f.negative === negative)
    : [];
  return (
    <>
      <aside>
        <div className="wordmark">
          z<span>eo</span>core
        </div>
        <p>COMPONENT LABORATORY</p>
        <nav aria-label="Situations">
          <button
            aria-current={filter === "all" ? "page" : undefined}
            onClick={() => setFilter("all")}
          >
            All situations <span>10</span>
          </button>
          {supportedSituations.map((s) => (
            <button
              key={s}
              aria-current={filter === s ? "page" : undefined}
              onClick={() => setFilter(s)}
            >
              {s.replaceAll("_", " ")}
            </button>
          ))}
          <button
            aria-current={calendar ? "page" : undefined}
            onClick={() => setFilter("calendar_view")}
          >
            calendar view
          </button>
        </nav>
        <small>
          Data-only components.
          <br />
          Authority stays with the owner.
        </small>
      </aside>
      <main>
        <header className="top">
          <span>FOUNDATIONS / EPHEMERAL UI</span>
          <span className="tag">LOCAL FIXTURES</span>
        </header>
        <section className="intro">
          <p className="kicker">THE RIGHT INTERFACE, AT THE RIGHT MOMENT</p>
          <h1>
            Small cards.
            <br />
            <em>Useful decisions.</em>
          </h1>
          <p>
            One shared language for work, messages and the things that need you.
            Source facts stay visible. Every action stays with its owner.
          </p>
        </section>
        <div className="toolbar">
          <h2>
            {filter === "all" ? "Every situation" : filter.replaceAll("_", " ")}
          </h2>
          <label>
            <input
              type="checkbox"
              checked={negative}
              onChange={(e) => setNegative(e.target.checked)}
            />{" "}
            Refusal fixtures
          </label>
          <label>
            <input
              type="checkbox"
              checked={demo}
              onChange={(e) => setDemo(e.target.checked)}
            />{" "}
            Local intent demo
          </label>
          <span>{rows.length + views.length} fixtures</span>
        </div>
        {intent && (
          <output className="intent" aria-live="polite">
            Local intent only ·{" "}
            {intent.kind === "reply" ? "Reply prepared" : intent.action.label}{" "}
            <button onClick={() => setIntent(undefined)}>Dismiss</button>
          </output>
        )}
        <div className="grid">
          {views.map((f) => (
            <section key={f.name}>
              <div className="fixture-label">
                {f.name.replace(".dataref.json", "").replace(".json", "")} ·{" "}
                {validateCalendar(f.value).ok ? "accepted" : "refused"}
              </div>
              <CommitmentCalendar value={f.value} />
            </section>
          ))}
          {rows.map((f) => (
            <section key={f.name}>
              <div className="fixture-label">
                {f.name.replace(".dataref.json", "").replace(".json", "")} ·{" "}
                {validateCard(f.value).ok ? "accepted" : "refused"}
              </div>
              <Card
                value={f.value}
                onIntent={demo ? setIntent : undefined}
                interaction={{
                  enabled: demo,
                  reason:
                    "Gallery preview. Enable local intent demo to try the controls.",
                }}
              />
            </section>
          ))}
        </div>
      </main>
    </>
  );
}
createRoot(document.getElementById("root")!).render(<Gallery />);
