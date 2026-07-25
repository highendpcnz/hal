import { defineConfig, devices } from "@playwright/test";

// End-to-end tests for the behaviour layer in static/index.html — the ~1,600
// lines of inline JS that tests/run.py can only pin with substring assertions.
//
// The server runs with HAL_SKIP_MODELS=1, the same discipline the Python suite
// uses: no STT/TTS models load and no inference happens, so the whole suite
// stays fast and offline. Everything these tests exercise (page boot, the
// direction runtime, the WebGL scene, /api/session/reset) is model-free.
// Anything that needs a real turn belongs in the smoke driver, not here.

const PORT = Number(process.env.HAL_E2E_PORT ?? 8123);

export default defineConfig({
  testDir: "tests/e2e",
  fullyParallel: false, // one app instance, and reset mutates session state
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? "list" : [["list"]],
  timeout: 45_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
    video: "off"
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        // getUserMedia would otherwise prompt and then reject in headless,
        // which is the only failure the duplex path cannot recover from.
        permissions: ["microphone"],
        launchOptions: {
          args: [
            // The scenes are WebGL; headless Chrome needs software
            // rasterisation, and without this the optic silently never boots.
            "--enable-unsafe-swiftshader",
            // A synthetic mic so full-duplex can actually be switched on.
            "--use-fake-device-for-media-stream",
            "--use-fake-ui-for-media-stream"
          ]
        }
      }
    }
  ],
  webServer: {
    // Bind loopback and pass the port through; HAL_DATA_DIR keeps the tests
    // from writing sessions and logs into the real ./data.
    command:
      `HAL_SKIP_MODELS=1 HAL_DATA_DIR=.playwright-data HAL_BOOT_RITUAL=0 ` +
      `.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
    // uvicorn logs every static asset; that drowns the test output. Failures
    // surface through stderr and the retained trace, which is what you want.
    stdout: "ignore",
    stderr: "pipe"
  }
});
