import type { Page, WebSocketRoute } from "@playwright/test";

export interface Socket {
  /** Frames the page sent us. */
  sent: string[];
  /** Push a server frame to the page. */
  send: (frame: unknown) => void;
  ready: Promise<void>;
}

/**
 * Stand in for /ws/conversation — the test becomes the bridge.
 *
 * Must be installed before navigation. Not calling `connectToServer` is the
 * point: we answer instead of the real server, so any frame it could emit is
 * reproducible without models, inference, or audio.
 */
export async function mockSocket(page: Page): Promise<Socket> {
  const sent: string[] = [];
  let route: WebSocketRoute | null = null;
  let markReady!: () => void;
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

  return { sent, send: (frame) => route?.send(JSON.stringify(frame)), ready };
}

/**
 * Serve SSE frames in place of the real /api/events stream.
 *
 * Only the first connection gets them. Fulfilling a route closes the
 * response, which trips the page's 5s reconnect — and a replayed body would
 * re-raise a prompt the user just answered, which the real bridge would never
 * do. Later connections get an inert keepalive.
 */
export async function serveEvents(page: Page, frames: unknown[]): Promise<void> {
  const body = frames.map((f) => `data: ${JSON.stringify(f)}\n\n`).join("");
  let delivered = false;
  await page.route("**/api/events", async (route) => {
    const payload = delivered ? ": keepalive\n\n" : body;
    delivered = true;
    await route.fulfill({
      status: 200,
      headers: { "content-type": "text/event-stream", "cache-control": "no-cache" },
      body: payload
    });
  });
}

/** Frames of a given type the page has sent, parsed. */
export function framesOfType(socket: Socket, type: string): Record<string, unknown>[] {
  return socket.sent
    .filter((raw) => raw.includes(`"${type}"`))
    .map((raw) => JSON.parse(raw) as Record<string, unknown>)
    .filter((frame) => frame.type === type);
}

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
