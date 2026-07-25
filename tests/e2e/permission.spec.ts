import { expect, test, type Page } from "@playwright/test";

import { logText, opticReady, pinDirection } from "./helpers";

/**
 * The Allow/Deny bar — the UI that gates tool execution.
 *
 * It is driven entirely by the SSE stream, so the test serves /api/events
 * itself and hands the page exactly the frames the bridge would publish in
 * HAL_PERMISSION_MODE=ask. Reaching this path for real would need a live
 * agent asking for a dangerous tool; mocking the stream makes it a
 * sub-second, deterministic check of the control that decides whether
 * commands run.
 */

const REQUEST_ID = "req-0123456789abcdef";

/**
 * Serve one SSE frame (or several) in place of the real event stream.
 *
 * Only the first connection gets the frames. Fulfilling a route closes the
 * response, which trips the page's `onerror` reconnect after 5s — and a
 * replayed body would re-raise a prompt the user just answered, which the
 * real bridge would never do. Later connections get an inert keepalive.
 */
async function serveEvents(page: Page, frames: unknown[]): Promise<void> {
  const body = frames.map((f) => `data: ${JSON.stringify(f)}\n\n`).join("");
  let delivered = false;
  await page.route("**/api/events", async (route) => {
    const payload = delivered ? ": keepalive\n\n" : body;
    delivered = true;
    await route.fulfill({
      status: 200,
      headers: {
        "content-type": "text/event-stream",
        "cache-control": "no-cache"
      },
      body: payload
    });
  });
}

async function openBridge(page: Page): Promise<void> {
  await pinDirection(page, "aperture");
  await page.goto("/");
  await opticReady(page);
}

const permissionRequest = {
  type: "permission_request",
  request_id: REQUEST_ID,
  tool_call_id: "tool-1",
  title: "Run shell command: rm -rf ~/Downloads/tmp",
  timeout: 30
};

test.describe("permission bar", () => {
  test("a permission_request raises the bar and names the tool", async ({ page }) => {
    await serveEvents(page, [permissionRequest]);
    await openBridge(page);

    await expect(page.locator("#permbar")).toHaveClass(/show/);
    await expect(page.locator("#permbar-text")).toContainText(
      "Run shell command: rm -rf ~/Downloads/tmp"
    );
    // Both controls must be present — a bar you can only accept is a trap.
    await expect(page.locator("#perm-allow")).toBeVisible();
    await expect(page.locator("#perm-deny")).toBeVisible();
    await expect.poll(() => logText(page)).toContain("awaiting permission");
  });

  test("Allow posts the decision for that exact request and lowers the bar", async ({ page }) => {
    await serveEvents(page, [permissionRequest]);
    await openBridge(page);
    await expect(page.locator("#permbar")).toHaveClass(/show/);

    const [request] = await Promise.all([
      page.waitForRequest(
        (r) => r.url().includes("/api/permission/") && r.method() === "POST"
      ),
      page.click("#perm-allow")
    ]);

    expect(decodeURIComponent(request.url())).toContain(`/api/permission/${REQUEST_ID}`);
    expect(JSON.parse(request.postData() ?? "{}")).toEqual({ decision: "allow" });
    await expect(page.locator("#permbar")).not.toHaveClass(/show/);
  });

  test("Deny posts deny — the default must never be inverted", async ({ page }) => {
    await serveEvents(page, [permissionRequest]);
    await openBridge(page);
    await expect(page.locator("#permbar")).toHaveClass(/show/);

    const [request] = await Promise.all([
      page.waitForRequest(
        (r) => r.url().includes("/api/permission/") && r.method() === "POST"
      ),
      page.click("#perm-deny")
    ]);

    expect(JSON.parse(request.postData() ?? "{}")).toEqual({ decision: "deny" });
    await expect(page.locator("#permbar")).not.toHaveClass(/show/);
  });

  test("a resolution from elsewhere lowers the bar and records the outcome", async ({ page }) => {
    // Answering by voice resolves the request server-side; the browser learns
    // about it through this event and must drop its stale prompt.
    await serveEvents(page, [
      permissionRequest,
      {
        type: "permission_resolved",
        request_id: REQUEST_ID,
        title: "Run shell command: rm -rf ~/Downloads/tmp",
        allowed: true
      }
    ]);
    await openBridge(page);

    await expect(page.locator("#permbar")).not.toHaveClass(/show/);
    await expect.poll(() => logText(page)).toContain("allowed");
  });

  test("a resolution for a different request leaves the bar up", async ({ page }) => {
    await serveEvents(page, [
      permissionRequest,
      {
        type: "permission_resolved",
        request_id: "some-other-request",
        title: "Unrelated tool",
        allowed: true
      }
    ]);
    await openBridge(page);

    // The pending prompt is still ours; a stranger's resolution must not clear it.
    await expect(page.locator("#permbar")).toHaveClass(/show/);
    await expect(page.locator("#permbar-text")).toContainText("rm -rf ~/Downloads/tmp");
  });

  test("a denial published by the server flashes the eye and is logged", async ({ page }) => {
    await serveEvents(page, [
      { type: "permission_denied", tool_call_id: "tool-9", title: "Delete the crew records" }
    ]);
    await openBridge(page);

    await expect.poll(() => logText(page)).toContain("permission denied");
    await expect.poll(() => logText(page)).toContain("Delete the crew records");
  });
});
