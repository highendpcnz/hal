import { expect, test, type Page } from "@playwright/test";

import {
  captionText,
  eyeState,
  framesOfType,
  logText,
  mockSocket,
  opticReady,
  pinDirection,
  type Socket
} from "./helpers";

/**
 * The full-duplex WebSocket protocol, from the client's side.
 *
 * `page.routeWebSocket` lets the test *be* the server, so every frame the
 * bridge can emit is reproducible without models, inference, or audio. That
 * matters because most of these frames are unreachable otherwise: `tts_done`
 * racing `turn_done` during commentary, `turn_aborted` with a wake-word
 * reason, an interim caption mid-recording. tests/run.py can only check that
 * the strings exist somewhere in index.html.
 */

async function openBridge(page: Page): Promise<Socket> {
  const socket = await mockSocket(page);
  await pinDirection(page, "aperture");
  await page.goto("/");
  await opticReady(page);
  await socket.ready;
  return socket;
}

test.describe("websocket protocol", () => {
  test("transcript frames land in the log and put the eye in speaking", async ({ page }) => {
    const socket = await openBridge(page);

    socket.send({ type: "transcript", role: "user", text: "Open the pod bay doors." });
    await expect
      .poll(() => logText(page))
      .toContain("Open the pod bay doors.");
    // A user frame alone must not claim HAL is speaking.
    expect(await eyeState(page)).not.toContain("speaking");

    socket.send({ type: "transcript", role: "hal", text: "I'm sorry, Dave." });
    await expect.poll(() => logText(page)).toContain("I'm sorry, Dave.");
    await expect.poll(() => eyeState(page)).toContain("speaking");
    expect(await captionText(page)).toContain("I'm sorry, Dave.");
  });

  test("typed input travels over the socket as text_input, not HTTP", async ({ page }) => {
    const socket = await openBridge(page);

    await page.fill("#bridge-cmd", "status report");
    await page.press("#bridge-cmd", "Enter");

    await expect.poll(() => socket.sent.filter((f) => f.includes("text_input")).length).toBe(1);
    const frame = JSON.parse(socket.sent.find((f) => f.includes("text_input"))!);
    expect(frame).toEqual({ type: "text_input", text: "status report" });
  });

  test("push-to-talk prefers the socket and preserves audio frame order", async ({ page }) => {
    let talkRequests = 0;
    page.on("request", (request) => {
      if (new URL(request.url()).pathname === "/api/talk") talkRequests += 1;
    });
    const socket = await openBridge(page);

    await page.dispatchEvent("#eye", "mousedown", { button: 0 });
    await expect.poll(() => eyeState(page)).toContain("listening");
    await page.waitForTimeout(300);
    await page.dispatchEvent("#eye", "mouseup", { button: 0 });

    await expect.poll(() => framesOfType(socket, "start_speech").length).toBe(1);
    expect(framesOfType(socket, "start_speech")[0]).toEqual({
      type: "start_speech",
      manual: true
    });
    await expect
      .poll(() => socket.sent.filter((frame) => frame === "<binary>").length)
      .toBeGreaterThanOrEqual(1);
    await expect.poll(() => framesOfType(socket, "end_speech").length).toBe(1);

    const start = socket.sent.findIndex((frame) => frame.includes("start_speech"));
    const firstAudio = socket.sent.indexOf("<binary>");
    const lastAudio = socket.sent.lastIndexOf("<binary>");
    const end = socket.sent.findIndex((frame) => frame.includes("end_speech"));
    expect(start).toBeLessThan(firstAudio);
    expect(lastAudio).toBeLessThan(end);
    expect(talkRequests).toBe(0);

    socket.send({ type: "turn_aborted", reason: "no_speech", text: "" });
    await expect.poll(() => eyeState(page)).toEqual([]);
  });

  test("the wake-mode frame is sent as soon as the socket opens", async ({ page }) => {
    const socket = await openBridge(page);
    await expect.poll(() => socket.sent.some((f) => f.includes("set_mode"))).toBe(true);
    const frame = JSON.parse(socket.sent.find((f) => f.includes("set_mode"))!);
    expect(frame.type).toBe("set_mode");
    expect(typeof frame.wake_word).toBe("boolean");
  });

  test("turn_aborted releases the turn and explains itself", async ({ page }) => {
    const socket = await openBridge(page);

    socket.send({ type: "transcript", role: "hal", text: "Working." });
    await expect.poll(() => eyeState(page)).toContain("speaking");

    socket.send({ type: "turn_aborted", reason: "no_speech", text: "I didn't quite catch that, Dave." });
    await expect.poll(() => eyeState(page)).toEqual([]);
    expect(await captionText(page)).toContain("didn't quite catch that");
  });

  test("wake-gated speech is dropped without comment", async ({ page }) => {
    const socket = await openBridge(page);

    // Put a caption on screen so we can prove the abort didn't replace it.
    socket.send({ type: "transcript", role: "hal", text: "Standing by." });
    await expect.poll(() => captionText(page)).toContain("Standing by.");

    socket.send({ type: "turn_aborted", reason: "no_wake_word", text: "" });
    await page.waitForTimeout(600);

    // Turn released, but nothing said about it — ambient speech is not an error.
    expect(await eyeState(page)).toEqual([]);
    expect(await captionText(page)).toContain("Standing by.");
  });

  test("during commentary, tts_done defers to turn_done", async ({ page }) => {
    const socket = await openBridge(page);

    // Commentary is speak-while-thinking: one tts cycle per sentence, so
    // tts_done arrives repeatedly mid-turn and must NOT release the turn.
    socket.send({ type: "commentary", text: "One moment, Dave." });
    await expect.poll(() => eyeState(page)).toContain("speaking");

    socket.send({ type: "tts_start", sample_rate: 22050 });
    socket.send({ type: "tts_done" });
    await page.waitForTimeout(900);
    expect(
      await eyeState(page),
      "tts_done during commentary must not end the turn"
    ).toContain("speaking");

    socket.send({ type: "turn_done" });
    await expect.poll(() => eyeState(page), { timeout: 8000 }).toEqual([]);
  });

  test("commentary phrases queue PCM instead of overlapping it", async ({ page }) => {
    const socket = await openBridge(page);
    await page.mouse.click(5, 5); // trusted gesture primes the AudioContext
    const oneSecondOfPcm = Buffer.alloc(22050 * 2);

    socket.send({ type: "commentary", text: "First phrase, Dave." });
    socket.send({ type: "tts_start", sample_rate: 22050 });
    socket.sendBinary(oneSecondOfPcm);
    socket.send({ type: "tts_done" });
    socket.send({ type: "commentary", text: "Second phrase, Dave." });
    socket.send({ type: "tts_start", sample_rate: 22050 });
    socket.sendBinary(oneSecondOfPcm);
    socket.send({ type: "tts_done" });
    socket.send({ type: "turn_done" });

    await expect.poll(() => eyeState(page)).toContain("speaking");
    await page.waitForTimeout(1300);
    expect(
      await eyeState(page),
      "two one-second phrases must still be playing after 1.3 seconds"
    ).toContain("speaking");
    await expect.poll(() => eyeState(page), { timeout: 5000 }).toEqual([]);
  });

  test("a tts cycle outside commentary does release the turn", async ({ page }) => {
    const socket = await openBridge(page);

    socket.send({ type: "transcript", role: "hal", text: "All systems nominal." });
    await expect.poll(() => eyeState(page)).toContain("speaking");

    socket.send({ type: "tts_start", sample_rate: 22050 });
    socket.send({ type: "tts_done" });
    await expect.poll(() => eyeState(page), { timeout: 8000 }).toEqual([]);
  });
});
