import { defineConfig, devices } from "@playwright/test";
import { pinSnapshotFontEnv } from "./tests/font-env";

pinSnapshotFontEnv();

export default defineConfig({
  testDir: "./tests/e2e",
  outputDir: "./test-results/e2e",
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? "github" : "list",
  // Pixel snapshots must not depend on the host clock zone: timestamps on
  // the page render through the browser context timezone, which otherwise
  // inherits the machine (UTC on CI, UTC+8 on the delivery host).
  use: {
    baseURL: "http://127.0.0.1:3100",
    trace: "retain-on-failure",
    timezoneId: "UTC",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: "npm run dev -- --port 3100",
      url: "http://127.0.0.1:3100",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: { TZ: "UTC" },
    },
    {
      command: "../../.venv/bin/python ../../tests/fixtures/session-browser.py",
      url: "http://127.0.0.1:3101/health",
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
});
