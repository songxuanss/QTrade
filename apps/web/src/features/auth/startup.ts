import {
  closeSync,
  constants,
  fstatSync,
  openSync,
  readFileSync,
} from "node:fs";
import { authConfig } from "./config";

const secretSettings = new Set([
  "DATABASE_URL",
  "MARKET_DATABASE_URL",
  "REDIS_URL",
  "OBJECT_STORE_ACCESS_KEY",
  "OBJECT_STORE_SECRET_KEY",
  "MAIRUI_LICENCE",
  "QTRADE_OIDC_CLIENT_SECRET",
  "QTRADE_BFF_SECRET",
  "QTRADE_OIDC_TRANSACTION_KEY",
  "QTRADE_SESSION_KEYS_JSON",
  "QTRADE_SESSION_PRIMARY_KEY",
  "SMTP_PASSWORD",
  "QTRADE_WEBHOOK_DESTINATIONS",
]);

export function validateStartup(
  env: Record<string, string | undefined> = process.env,
) {
  const mode = env.QTRADE_ENV ?? "local";
  if (!["local", "dev", "test", "staging", "production"].includes(mode))
    throw new Error("QTRADE_ENV must name a supported environment");
  const protectedEnvironment = ["staging", "production"].includes(mode);
  if (env.QTRADE_SECRETS_FILE) {
    let descriptor: number | undefined;
    try {
      descriptor = openSync(
        env.QTRADE_SECRETS_FILE,
        constants.O_RDONLY | constants.O_NOFOLLOW,
      );
      const info = fstatSync(descriptor);
      if (
        !info.isFile() ||
        (info.mode & 0o077) !== 0 ||
        info.size > 65_536 ||
        ![0, process.getuid?.()].includes(info.uid)
      )
        throw new Error();
      const values: unknown = JSON.parse(readFileSync(descriptor, "utf8"));
      if (!values || typeof values !== "object" || Array.isArray(values))
        throw new Error();
      const entries = Object.entries(values);
      if (
        !entries.length ||
        entries.some(
          ([key, value]) =>
            !secretSettings.has(key) || typeof value !== "string" || !value,
        )
      )
        throw new Error();
      Object.assign(env, values);
    } catch {
      throw new Error("QTRADE_SECRETS_FILE must be a private, valid JSON file");
    } finally {
      if (descriptor !== undefined) closeSync(descriptor);
    }
  } else if (protectedEnvironment) {
    throw new Error("QTRADE_SECRETS_FILE is required in staging/production");
  }
  for (const key of [
    "QTRADE_OIDC_ALLOW_HTTP",
    "QTRADE_INSECURE_LOCAL_COOKIES",
    "QTRADE_LIVE_TRADING_ENABLED",
  ]) {
    if (!["true", "false"].includes(env[key] ?? "false"))
      throw new Error(`${key} must be true or false`);
  }
  if (env.QTRADE_LIVE_TRADING_ENABLED === "true")
    throw new Error("Live adapter is not configured; keep Live disabled");
  if (protectedEnvironment) {
    if (!env.QTRADE_CONFIG_VERSION || !env.QTRADE_API_PUBLIC_ORIGIN)
      throw new Error(
        "QTRADE_CONFIG_VERSION and QTRADE_API_PUBLIC_ORIGIN are required",
      );
    authConfig(env);
  }
}
