import { defineConfig, devices } from "@playwright/test";
import { pinSnapshotFontEnv } from "./tests/font-env";

pinSnapshotFontEnv();

export default defineConfig({
  testDir: "./tests/auth",
  outputDir: "./test-results/auth",
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  // See playwright.config.ts: pin the browser context timezone so timestamp
  // pixels are identical on CI (UTC) and the delivery host (UTC+8).
  use: {
    baseURL: "http://127.0.0.1:3110",
    trace: "retain-on-failure",
    timezoneId: "UTC",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: "../../.venv/bin/python ../../tests/fixtures/oidc-browser.py",
      url: "http://127.0.0.1:3111/health",
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: "node tests/serve-production.mjs",
      url: "http://127.0.0.1:3110/health",
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        PORT: "3110",
        HOSTNAME: "127.0.0.1",
        TZ: "UTC",
        QTRADE_ENV: "test",
        QTRADE_OIDC_ALLOW_HTTP: "true",
        QTRADE_INSECURE_LOCAL_COOKIES: "true",
        QTRADE_OIDC_ISSUER: "http://127.0.0.1:3111",
        QTRADE_OIDC_CLIENT_ID: "client",
        QTRADE_OIDC_CLIENT_SECRET: "browser-fixture-client-secret",
        QTRADE_BFF_SECRET: "browser-fixture-bff-secret-1234567890123456",
        QTRADE_OIDC_TRANSACTION_KEY: Buffer.alloc(32, 7).toString("base64"),
        QTRADE_PUBLIC_ORIGIN: "http://127.0.0.1:3110",
        QTRADE_API_ORIGIN: "http://127.0.0.1:3111",
        QTRADE_API_PUBLIC_ORIGIN: "http://127.0.0.1:3111",
      },
    },
  ],
});
