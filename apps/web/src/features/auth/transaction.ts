import { createCipheriv, createDecipheriv, randomBytes } from "node:crypto";
import type { AuthConfig } from "./config";

export interface LoginTransaction {
  state: string;
  nonce: string;
  verifier: string;
  returnTo: string;
  expiresAt: number;
}

function aad(config: AuthConfig) {
  return Buffer.from(
    `qtrade/oidc/v1:${config.publicOrigin}:${config.clientId}:${config.issuer}`,
  );
}
export function sealTransaction(
  config: AuthConfig,
  transaction: LoginTransaction,
): string {
  const nonce = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", config.transactionKey, nonce);
  cipher.setAAD(aad(config));
  const ciphertext = Buffer.concat([
    cipher.update(JSON.stringify(transaction), "utf8"),
    cipher.final(),
  ]);
  return Buffer.concat([nonce, cipher.getAuthTag(), ciphertext]).toString(
    "base64url",
  );
}
export function openTransaction(
  config: AuthConfig,
  cookie: string | undefined,
  now = Date.now(),
): LoginTransaction {
  if (!cookie || cookie.length > 2048 || !/^[A-Za-z0-9_-]+$/.test(cookie))
    throw new Error("Invalid login transaction");
  try {
    const bytes = Buffer.from(cookie, "base64url");
    const decipher = createDecipheriv(
      "aes-256-gcm",
      config.transactionKey,
      bytes.subarray(0, 12),
    );
    decipher.setAAD(aad(config));
    decipher.setAuthTag(bytes.subarray(12, 28));
    const parsed = JSON.parse(
      Buffer.concat([
        decipher.update(bytes.subarray(28)),
        decipher.final(),
      ]).toString("utf8"),
    );
    if (
      typeof parsed.expiresAt !== "number" ||
      parsed.expiresAt <= now ||
      parsed.expiresAt > now + 300_000 ||
      ![parsed.state, parsed.nonce, parsed.verifier].every(
        (value) =>
          typeof value === "string" && /^[A-Za-z0-9_-]{43,128}$/.test(value),
      ) ||
      typeof parsed.returnTo !== "string"
    )
      throw new Error("Invalid login transaction");
    return parsed as LoginTransaction;
  } catch {
    throw new Error("Invalid login transaction");
  }
}
export function transactionCookieName(config: AuthConfig) {
  return config.secureCookies ? "__Host-qtrade-oidc" : "qtrade-oidc";
}
