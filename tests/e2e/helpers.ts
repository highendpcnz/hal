import type { Page } from "@playwright/test";

/** Pin the direction before any page script runs — the pre-paint script reads
 *  localStorage to pick the stylesheet, so this has to land first. */
export async function pinDirection(page: Page, direction: string): Promise<void> {
  await page.addInitScript((d) => {
    try {
      localStorage.setItem("hal_direction", d);
    } catch {
      /* storage unavailable; the runtime falls back and the test will show it */
    }
  }, direction);
}

/** The scene is a dynamic import — wait for the direction runtime to boot. */
export async function opticReady(page: Page): Promise<void> {
  await page.waitForFunction(() => Boolean((window as { HALOptic?: unknown }).HALOptic), null, {
    timeout: 20_000
  });
}

/** Classes the behaviour layer puts on #eye — the only externally visible
 *  signal of turn state, since `busy` is a closure variable. */
export async function eyeState(page: Page): Promise<string[]> {
  return page.evaluate(() =>
    Array.from(document.getElementById("eye")?.classList ?? []).filter((c) =>
      ["listening", "thinking", "speaking", "denied"].includes(c)
    )
  );
}

export async function logText(page: Page): Promise<string> {
  return page.evaluate(() => document.getElementById("mlog-entries")?.textContent ?? "");
}

/** Visible caption text, or "" when the caption is not showing. */
export async function captionText(page: Page): Promise<string> {
  return page.evaluate(() => {
    const el = document.querySelector(".caption");
    if (!el || !el.classList.contains("show")) return "";
    return el.textContent ?? "";
  });
}
