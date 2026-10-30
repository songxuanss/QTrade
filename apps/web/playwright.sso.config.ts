import { readFileSync } from "node:fs";
import { defineConfig, devices } from "@playwright/test";

const state = JSON.parse(
  readFileSync("../../.tools/sso-local/state.json", "utf8"),
);
export default defineConfig({
  testDir: "./tests/sso",
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  use: {
    baseURL: "http://127.0.0.1:3120",
    trace: "off",
    screenshot: "off",
    video: "off",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command:
        "../../.tools/sso-venv/bin/python ../../scripts/sso-local.py serve",
      url: "http://127.0.0.1:3121/healthz",
      reuseExistingServer: false,
    },
    {
      command: "../../.venv/bin/python ../../scripts/sso-local-api.py",
      url: "http://127.0.0.1:3122/health",
      reuseExistingServer: false,
    },
    {
      command: "node tests/serve-production.mjs",
      url: "http://127.0.0.1:3120/health",
      reuseExistingServer: false,
      env: {
        PORT: "3120",
        HOSTNAME: "127.0.0.1",
        QTRADE_ENV: "test",
        QTRADE_OIDC_ALLOW_HTTP: "true",
        QTRADE_INSECURE_LOCAL_COOKIES: "true",
        QTRADE_OIDC_ISSUER: state.issuer,
        QTRADE_OIDC_CLIENT_ID: state.client_id,
        QTRADE_OIDC_CLIENT_SECRET: state.client_secret,
        QTRADE_BFF_SECRET: state.bridge_secret,
        QTRADE_OIDC_TRANSACTION_KEY: state.transaction_key,
        QTRADE_PUBLIC_ORIGIN: "http://127.0.0.1:3120",
        QTRADE_API_ORIGIN: "http://127.0.0.1:3122",
        QTRADE_API_PUBLIC_ORIGIN: "http://127.0.0.1:3122",
      },
    },
  ],
});
