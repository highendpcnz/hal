import { expect, test, type Page, type WebSocketRoute } from "@playwright/test";

import { captionText, eyeState, logText, opticReady, pinDirection } from "./helpers";

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

interface Socket {
  /** Frames the page sent us. */
  sent: string[];
  /** Push a server frame to the page. */
  send: (frame: unknown) => void;
  ready: Promise<void>;
}

/** Stand in for /ws/conversation. Must be installed before navigation. */
async function mockSocket(page: Page): Promise<Socket> {
  const sent: string[] = [];
  let route: WebSocketRoute | null = null;
  let markReady: () => void;
  const ready = new Promise<void>((resolve) => {
    markReady = resolve;
  });

  await page.routeWebSocket(/\/ws\/conversation/, (ws) => {
    route = ws;
    ws.onMessage((message) => {
      sent.push(typeof message === "string" ? message : "<binary>");
    });
    markReady();
  });

  return {
    sent,
    send: (frame: unknown) => route?.send(JSON.stringify(frame)),
    ready
  };
}

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

  test("a tts cycle outside commentary does release the turn", async ({ page }) => {
    const socket = await openBridge(page);

    socket.send({ type: "transcript", role: "hal", text: "All systems nominal." });
    await expect.poll(() => eyeState(page)).toContain("speaking");

    socket.send({ type: "tts_start", sample_rate: 22050 });
    socket.send({ type: "tts_done" });
    await expect.poll(() => eyeState(page), { timeout: 8000 }).toEqual([]);
  });
});
