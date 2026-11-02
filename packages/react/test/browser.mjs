import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
import { chromium } from "playwright";
const root = fileURLToPath(new URL("..", import.meta.url));
const server = spawn(
  process.execPath,
  [
    root + "/node_modules/vite/bin/vite.js",
    root + "/gallery",
    "--host",
    "127.0.0.1",
    "--port",
    "5189",
    "--strictPort",
  ],
  { stdio: ["ignore", "pipe", "pipe"] },
);
let browser;
try {
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(
      () => reject(new Error("Gallery startup timed out")),
      15000,
    );
    server.stdout.on("data", (d) => {
      if (String(d).includes("Local:")) {
        clearTimeout(timeout);
        resolve();
      }
    });
    server.on("exit", () => {
      clearTimeout(timeout);
      reject(new Error("Gallery exited"));
    });
  });
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("http://127.0.0.1:5189");
  await page.waitForSelector(".zc-card");
  assert.equal(await page.locator(".zc-card").count(), 27);
  await page.addScriptTag({ path: root + "/node_modules/axe-core/axe.min.js" });
  const report = await page.evaluate(async () =>
    window.axe.run(document, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa"] },
    }),
  );
  assert.deepEqual(
    report.violations.map((v) => ({
      id: v.id,
      impact: v.impact,
      targets: v.nodes.map((n) => n.target),
    })),
    [],
  );
  await page.getByRole("button", { name: "message", exact: true }).click();
  assert.ok((await page.locator("form button:disabled").count()) > 0);
  await page.getByLabel("Local intent demo").check();
  const form = page
    .locator("form")
    .filter({ has: page.locator("textarea") })
    .first();
  await form.locator("textarea").fill("Please show the evidence.");
  await form.getByRole("button").click();
  assert.match(
    await page.locator("output.intent").innerText(),
    /Reply prepared/,
  );
  await page.getByRole("button", { name: "approval", exact: true }).click();
  assert.equal(await page.locator(".zc-card button").count(), 0);
  await page.setViewportSize({ width: 390, height: 844 });
  for (const situation of ["decision", "message", "project checkpoint"]) {
    await page.getByRole("button", { name: situation, exact: true }).click();
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
      false,
      situation,
    );
  }
  await page
    .getByRole("button", { name: "calendar view", exact: true })
    .click();
  assert.equal(await page.locator(".zc-cal:not(.zc-unavailable)").count(), 6);
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
    false,
    "calendar view",
  );
  const calendarReport = await page.evaluate(async () =>
    window.axe.run(document, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa"] },
    }),
  );
  assert.deepEqual(
    calendarReport.violations.map((v) => ({
      id: v.id,
      impact: v.impact,
      targets: v.nodes.map((n) => n.target),
    })),
    [],
  );
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.keyboard.press("Tab");
  assert.notEqual(
    await page.evaluate(() => document.activeElement?.tagName),
    "BODY",
  );
  assert.deepEqual(errors, []);
  const locked = await browser.newPage();
  await locked.route("**/*", async (route) => {
    if (route.request().resourceType() !== "document") return route.continue();
    const response = await route.fetch();
    await route.fulfill({
      response,
      headers: {
        ...response.headers(),
        "content-security-policy":
          "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' ws:; img-src 'self' data:",
      },
    });
  });
  await locked.goto("http://127.0.0.1:5189");
  await locked.waitForSelector(".zc-card");
  assert.equal(await locked.locator(".zc-card").count(), 27);
  await locked.close();
  if (process.env.ZEO_UI_SCREENSHOTS) {
    await mkdir(process.env.ZEO_UI_SCREENSHOTS, { recursive: true });
    await page.getByRole("button", { name: "message", exact: true }).click();
    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({
      path: process.env.ZEO_UI_SCREENSHOTS + "/mobile.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page
      .getByRole("button", { name: "All situations 10", exact: true })
      .click();
    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({
      path: process.env.ZEO_UI_SCREENSHOTS + "/desktop.png",
    });
  }
  console.log(
    "Browser passed: 27 fixtures, 6 calendar views, WCAG A/AA scan, reply intent, display-only approvals, phone overflow, keyboard focus, reduced motion, no page errors.",
  );
} finally {
  await browser?.close();
  server.kill();
}
