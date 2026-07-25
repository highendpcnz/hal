import { expect, test, type Page } from "@playwright/test";

import {
  eyeState as eyeStates,
  framesOfType,
  logText,
  mockSocket,
  opticReady,
  pinDirection,
  type Socket
} from "./helpers";

// index.html's behaviour layer is a classic <script>, so its top-level
// let/const are script-scoped and never become window properties. Bare
// identifiers resolve inside page.evaluate; window.* does not.
declare let isWsRecording: boolean;
declare const setState: (state: string) => void;

/**
 * Full-duplex mode and the VAD.
 *
 * Runs against a fake microphone (`--use-fake-device-for-media-stream`), so
 * getUserMedia resolves and the whole mic path — AudioContext, analyser,
 * waveform, VAD loop — actually runs instead of falling into its error
 * branch. The socket is mocked so the `set_mode` handshake can be inspected
 * frame by frame.
 *
 * The mode is a three-way cycle (OFF → ON → WAKE → OFF) whose wake-word flag
 * the server relies on to decide whether ambient speech reaches the agent at
 * all. Getting that flag wrong either deafens HAL or lets every overheard
 * sentence through, and nothing checked it before.
 */

async function openBridge(page: Page): Promise<Socket> {
  const socket = await mockSocket(page);
  await pinDirection(page, "aperture");
  await page.goto("/");
  await opticReady(page);
  await socket.ready;
  return socket;
}

const duplex = "#mlog-duplex";

test.describe("full duplex", () => {
  test("the mode cycles OFF → ON → WAKE → OFF and says so", async ({ page }) => {
    const socket = await openBridge(page);
    await expect(page.locator(duplex)).toHaveText(/OFF/i);

    await page.click(duplex);
    await expect(page.locator(duplex)).toHaveText(/ON/i);

    await page.click(duplex);
    await expect(page.locator(duplex)).toHaveText(/WAKE/i);

    await page.click(duplex);
    await expect(page.locator(duplex)).toHaveText(/OFF/i);
    expect(socket.sent.length).toBeGreaterThan(0);
  });

  test("each mode change hands the server the right wake_word flag", async ({ page }) => {
    const socket = await openBridge(page);
    // One set_mode is sent on socket open, before any interaction.
    await expect.poll(() => framesOfType(socket, "set_mode").length).toBeGreaterThanOrEqual(1);
    expect(framesOfType(socket, "set_mode").at(-1)!.wake_word).toBe(false);

    await page.click(duplex); // ON — hot mic, but everything is heard
    await expect.poll(() => framesOfType(socket, "set_mode").at(-1)!.wake_word).toBe(false);

    await page.click(duplex); // WAKE — only "HAL, …" gets through
    await expect.poll(() => framesOfType(socket, "set_mode").at(-1)!.wake_word).toBe(true);

    await page.click(duplex); // OFF
    await expect.poll(() => framesOfType(socket, "set_mode").at(-1)!.wake_word).toBe(false);
  });

  test("switching on acquires the microphone and reports a hot mic", async ({ page }) => {
    const socket = await openBridge(page);
    await page.click(duplex);

    // The success branch of enableFullDuplex — the error branch says
    // "Microphone access denied", so this distinguishes them.
    await expect.poll(() => logText(page), { timeout: 15_000 }).toContain("Mic is hot");
    expect(await logText(page)).not.toContain("Microphone access denied");

    // The live waveform only runs off a real MediaStream.
    const streaming = await page.evaluate(() => {
      const canvas = document.querySelector<HTMLCanvasElement>(".waveform-canvas");
      return Boolean(canvas && canvas.width > 0);
    });
    expect(streaming).toBe(true);
    expect(socket.sent.some((f) => f.includes("set_mode"))).toBe(true);
  });

  test("wake mode announces its gate rather than silently swallowing speech", async ({ page }) => {
    await openBridge(page);
    await page.click(duplex);
    await expect.poll(() => logText(page), { timeout: 15_000 }).toContain("Mic is hot");

    await page.click(duplex);
    await expect.poll(() => logText(page)).toContain('say "HAL');
  });

  test("switching off releases the microphone tracks", async ({ page }) => {
    // Count real MediaStreamTrack.stop() calls. A mic left live after
    // disabling is the failure that actually matters: the tab keeps its
    // recording indicator and goes on listening with no UI admitting it.
    await page.addInitScript(() => {
      (window as unknown as { __trackStops: number }).__trackStops = 0;
      const original = MediaStreamTrack.prototype.stop;
      MediaStreamTrack.prototype.stop = function patched(this: MediaStreamTrack) {
        (window as unknown as { __trackStops: number }).__trackStops += 1;
        return original.call(this);
      };
    });

    await openBridge(page);
    await page.click(duplex);
    await expect.poll(() => logText(page), { timeout: 15_000 }).toContain("Mic is hot");
    expect(
      await page.evaluate(() => (window as unknown as { __trackStops: number }).__trackStops)
    ).toBe(0);

    await page.click(duplex); // WAKE — mic stays hot, gate changes
    await page.click(duplex); // OFF — mic must be released

    await expect.poll(() => logText(page)).toContain("Duplex mode disabled");
    await expect
      .poll(() =>
        page.evaluate(() => (window as unknown as { __trackStops: number }).__trackStops)
      )
      .toBeGreaterThan(0);
    await expect(page.locator(duplex)).toHaveText(/OFF/i);
  });

  test("switching off mid-capture releases the turn instead of wedging it", async ({ page }) => {
    await openBridge(page);
    await page.click(duplex);
    await expect.poll(() => logText(page), { timeout: 15_000 }).toContain("Mic is hot");

    // Stand in for a VAD capture in flight. The fake mic emits a steady tone
    // rather than speech, so waiting for the VAD to trip on its own would be
    // a coin flip; setting the flag exercises the same branch deterministically.
    // (Bare identifier, not window.* — see the scope note in chess.spec.ts.)
    await page.evaluate(() => {
      isWsRecording = true;
      setState("listening");
    });
    await expect.poll(() => eyeStates(page)).toContain("listening");

    await page.click(duplex); // WAKE
    await page.click(duplex); // OFF — the isWsRecording branch must call turnDone()

    await expect.poll(() => eyeStates(page)).toEqual([]);
    expect(await page.evaluate(() => isWsRecording)).toBe(false);
  });
});
