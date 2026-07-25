import { expect, test, type Page } from "@playwright/test";

/**
 * The session-reset path, end to end.
 *
 * tests/run.py can only assert that the *string* `playSessionEnd` appears in
 * resetSession's body. What it cannot reach is the thing that actually
 * matters: that clicking New Session runs the direction's farewell, waits for
 * it, and only then reloads into a fresh session — through a confirm() dialog
 * that blocks every other automation tool this repo has.
 */

const MARKERS = {
  loads: "e2e_loads",
  called: "e2e_sessionEnd_called",
  resolved: "e2e_sessionEnd_resolved"
} as const;

/**
 * Instrument before any page script runs.
 *
 * `window.HALOptic` is assigned by hal-optic.ts after its dynamic scene import
 * resolves, so there is no moment we could reliably patch it from the outside.
 * A property setter catches the assignment whenever it happens. Markers go in
 * sessionStorage because the page reloads out from under us and anything on
 * `window` dies with it.
 */
async function instrument(page: Page, direction: string): Promise<void> {
  await page.addInitScript(
    ({ direction, MARKERS }) => {
      try {
        localStorage.setItem("hal_direction", direction);
      } catch {
        /* storage unavailable — the direction runtime falls back, test will show it */
      }
      const loads = Number(sessionStorage.getItem(MARKERS.loads) ?? "0");
      sessionStorage.setItem(MARKERS.loads, String(loads + 1));

      let real: unknown;
      Object.defineProperty(window, "HALOptic", {
        configurable: true,
        get: () => real,
        set: (value: Record<string, unknown> | undefined) => {
          real = value;
          if (value && typeof value.playSessionEnd === "function") {
            const original = (value.playSessionEnd as () => Promise<void>).bind(value);
            value.playSessionEnd = async (): Promise<void> => {
              sessionStorage.setItem(MARKERS.called, String(Date.now()));
              await original();
              sessionStorage.setItem(MARKERS.resolved, String(Date.now()));
            };
          }
        }
      });
    },
    { direction, MARKERS }
  );
}

async function markers(page: Page): Promise<Record<string, string | null>> {
  return page.evaluate(
    (keys) =>
      Object.fromEntries(
        Object.entries(keys).map(([name, key]) => [name, sessionStorage.getItem(key as string)])
      ),
    MARKERS
  );
}

/** The scene is a dynamic import; wait for the direction runtime to finish booting. */
async function opticReady(page: Page): Promise<void> {
  await page.waitForFunction(() => Boolean((window as { HALOptic?: unknown }).HALOptic), null, {
    timeout: 20_000
  });
}

async function sessionCookie(page: Page): Promise<string | undefined> {
  const cookies = await page.context().cookies();
  return cookies.find((c) => c.name === "hal_session")?.value;
}

test.describe("session reset", () => {
  test("direction 05 plays the extraction, then reloads into a fresh session", async ({ page }) => {
    await instrument(page, "lmc");
    await page.goto("/");
    await opticReady(page);

    expect(await page.evaluate(() => document.documentElement.dataset.bridgeDirection)).toBe("lmc");
    const before = await sessionCookie(page);
    expect(before).toBeTruthy();

    // The confirm() that blocks every other tool. Accepting it is the point.
    page.once("dialog", (dialog) => {
      expect(dialog.type()).toBe("confirm");
      void dialog.accept();
    });

    await page.click("#mlog-reset");
    // Reload lands us back on a fresh page; the load counter proves it happened.
    await page.waitForFunction(
      (key) => Number(sessionStorage.getItem(key) ?? "0") >= 2,
      MARKERS.loads,
      { timeout: 30_000 }
    );
    await opticReady(page);

    const seen = await markers(page);
    expect(seen.called, "playSessionEnd should have been invoked").toBeTruthy();
    expect(seen.resolved, "resetSession should await it before reloading").toBeTruthy();
    expect(Number(seen.resolved)).toBeGreaterThanOrEqual(Number(seen.called));

    // The extraction ran for a real duration rather than resolving instantly.
    expect(Number(seen.resolved) - Number(seen.called)).toBeGreaterThan(300);

    // And the server actually issued a new session.
    const after = await sessionCookie(page);
    expect(after).toBeTruthy();
    expect(after).not.toBe(before);
  });

  test("the extraction is skippable — a keypress cuts it short", async ({ page }) => {
    await instrument(page, "lmc");
    await page.goto("/");
    await opticReady(page);

    // Drive the hook directly here: this asserts the skip affordance, not the
    // reset wiring (covered above), and avoids a second confirm() round-trip.
    const elapsed = await page.evaluate(async () => {
      const optic = (window as { HALOptic?: { playSessionEnd?: () => Promise<void> } }).HALOptic;
      const started = performance.now();
      const pending = optic!.playSessionEnd!();
      await new Promise((r) => setTimeout(r, 250));
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
      await pending;
      return performance.now() - started;
    });

    // Full run is ~2500ms; a skip must land well inside that.
    expect(elapsed).toBeLessThan(1500);
    expect(elapsed).toBeGreaterThan(200);
  });

  test("directions without the hook still reset — the contract is optional", async ({ page }) => {
    await instrument(page, "aperture");
    await page.goto("/");
    await opticReady(page);

    expect(await page.evaluate(() => document.documentElement.dataset.bridgeDirection)).toBe(
      "aperture"
    );
    expect(
      await page.evaluate(
        () =>
          typeof (window as { HALOptic?: { playSessionEnd?: unknown } }).HALOptic?.playSessionEnd
      ),
      "01-04 must not implement playSessionEnd"
    ).toBe("undefined");

    const before = await sessionCookie(page);
    page.once("dialog", (dialog) => void dialog.accept());
    await page.click("#mlog-reset");
    await page.waitForFunction(
      (key) => Number(sessionStorage.getItem(key) ?? "0") >= 2,
      MARKERS.loads,
      { timeout: 30_000 }
    );

    const seen = await markers(page);
    expect(seen.called, "no hook should have been invoked").toBeNull();
    expect(await sessionCookie(page)).not.toBe(before);
  });

  test("declining the confirm leaves the session alone", async ({ page }) => {
    await instrument(page, "lmc");
    await page.goto("/");
    await opticReady(page);

    const before = await sessionCookie(page);
    page.once("dialog", (dialog) => void dialog.dismiss());
    await page.click("#mlog-reset");
    await page.waitForTimeout(1500);

    const seen = await markers(page);
    expect(seen.called, "a declined reset must not play the extraction").toBeNull();
    expect(Number(seen.loads), "and must not reload").toBe(1);
    expect(await sessionCookie(page)).toBe(before);
  });
});
