import {
  chmodSync,
  mkdtempSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { authConfig } from "../../src/features/auth/config";
import { validateStartup } from "../../src/features/auth/startup";

const folders: string[] = [];
afterEach(() =>
  folders
    .splice(0)
    .forEach((folder) => rmSync(folder, { recursive: true, force: true })),
);

describe("deployment startup", () => {
  it("rejects missing protected identity and unsafe flags", () => {
    expect(() => authConfig({ QTRADE_ENV: "production" })).toThrow(
      "QTRADE_OIDC_ISSUER",
    );
    expect(() => validateStartup({ QTRADE_ENV: "production" })).toThrow(
      "QTRADE_SECRETS_FILE",
    );
    expect(() =>
      validateStartup({
        QTRADE_ENV: "local",
        QTRADE_LIVE_TRADING_ENABLED: "true",
      }),
    ).toThrow("Live adapter");
    expect(() =>
      validateStartup({ QTRADE_ENV: "local", QTRADE_OIDC_ALLOW_HTTP: "yes" }),
    ).toThrow("true or false");
  });
  it("loads private server secrets and rejects public files and links", () => {
    const folder = mkdtempSync(path.join(tmpdir(), "qtrade-startup-"));
    folders.push(folder);
    const file = path.join(folder, "private.secrets.json");
    writeFileSync(
      file,
      JSON.stringify({ QTRADE_BFF_SECRET: "private-value" }),
      { mode: 0o600 },
    );
    const env = {
      QTRADE_ENV: "local",
      QTRADE_SECRETS_FILE: file,
    } as Record<string, string | undefined>;
    validateStartup(env);
    expect(env.QTRADE_BFF_SECRET).toBe("private-value");
    chmodSync(file, 0o644);
    expect(() => validateStartup(env)).toThrow("private, valid JSON");
    chmodSync(file, 0o600);
    const link = path.join(folder, "link");
    symlinkSync(file, link);
    expect(() =>
      validateStartup({ ...env, QTRADE_SECRETS_FILE: link }),
    ).toThrow("private, valid JSON");
  });
});
