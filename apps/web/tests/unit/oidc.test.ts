import { createServer, type Server } from "node:http";
import { generateKeyPairSync, createHash, sign } from "node:crypto";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import {
  authConfig,
  safeReturnTo,
  type AuthConfig,
} from "../../src/features/auth/config";
import {
  verifyLogout,
  LOGOUT_EVENT,
} from "../../src/features/auth/backchannel";
import { beginLogin, completeLogin } from "../../src/features/auth/oidc";
import {
  openTransaction,
  sealTransaction,
} from "../../src/features/auth/transaction";

const keys = generateKeyPairSync("rsa", { modulusLength: 2048 });
const wrong = generateKeyPairSync("rsa", { modulusLength: 2048 });
const jwk = {
  ...keys.publicKey.export({ format: "jwk" }),
  kid: "test",
  use: "sig",
  alg: "RS256",
};
let server: Server;
let issuer: string;
let config: AuthConfig;
let exchanges = 0;
let tokenResponses = 0;
const codes = new Map<
  string,
  { nonce: string; verifier: string; mode: string }
>();
function jwt(claims: object, badSignature = false) {
  const header = Buffer.from(
    JSON.stringify({ alg: "RS256", kid: "test" }),
  ).toString("base64url");
  const payload = Buffer.from(JSON.stringify(claims)).toString("base64url");
  const content = header + "." + payload;
  return (
    content +
    "." +
    sign(
      "RSA-SHA256",
      Buffer.from(content),
      badSignature ? wrong.privateKey : keys.privateKey,
    ).toString("base64url")
  );
}

beforeAll(async () => {
  server = createServer(async (request, response) => {
    response.setHeader("Content-Type", "application/json");
    if (request.url === "/.well-known/openid-configuration") {
      response.end(
        JSON.stringify({
          issuer,
          authorization_endpoint: issuer + "/authorize",
          token_endpoint: issuer + "/token",
          jwks_uri: issuer + "/jwks",
          response_types_supported: ["code"],
          subject_types_supported: ["public"],
          id_token_signing_alg_values_supported: ["RS256"],
          token_endpoint_auth_methods_supported: ["client_secret_basic"],
          code_challenge_methods_supported: ["S256"],
        }),
      );
    } else if (request.url === "/jwks") {
      response.end(JSON.stringify({ keys: [jwk] }));
    } else if (request.url === "/token") {
      exchanges++;
      let body = "";
      for await (const chunk of request) body += chunk.toString();
      const params = new URLSearchParams(body);
      const item = codes.get(params.get("code") ?? "");
      if (
        !item ||
        item.verifier !== params.get("code_verifier") ||
        !request.headers.authorization?.startsWith("Basic ") ||
        Buffer.from(request.headers.authorization.slice(6), "base64")
          .toString()
          .split(":")
          .map(decodeURIComponent)
          .join(":") !== "client:server-secret"
      ) {
        response.statusCode = 400;
        response.end(JSON.stringify({ error: "invalid_grant" }));
        return;
      }
      tokenResponses++;
      codes.delete(params.get("code")!);
      const now = Math.floor(Date.now() / 1000);
      const claims = {
        iss: item.mode === "issuer" ? "https://wrong.test" : issuer,
        aud: item.mode === "audience" ? "other-client" : "client",
        sub: "subject-one",
        iat: now,
        exp: item.mode === "expiry" ? now - 600 : now + 300,
        nonce: item.mode === "nonce" ? "wrong" : item.nonce,
        name: "Test user",
        email: "verified@example.test",
        email_verified: true,
      };
      response.end(
        JSON.stringify({
          access_token: "private-access",
          refresh_token: "private-refresh",
          token_type: "Bearer",
          expires_in: 300,
          id_token: jwt(claims, item.mode === "signature"),
        }),
      );
    } else {
      response.statusCode = 404;
      response.end("{}");
    }
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string")
    throw Error("test server missing");
  issuer = `http://127.0.0.1:${address.port}`;
  config = {
    issuer,
    clientId: "client",
    clientSecret: "server-secret",
    publicOrigin: "http://127.0.0.1:3100",
    apiOrigin: "http://127.0.0.1:3101",
    bridgeSecret: "b".repeat(43),
    transactionKey: Buffer.alloc(32, 7),
    allowHttp: true,
    secureCookies: false,
  };
});
afterAll(async () => {
  await new Promise<void>((resolve) => server.close(() => resolve()));
});

async function callback(mode = "valid") {
  const { url, transaction } = await beginLogin(
    config,
    "/workspace/strategies",
  );
  expect(url.searchParams.get("code_challenge_method")).toBe("S256");
  expect(url.searchParams.get("code_challenge")).toBe(
    createHash("sha256").update(transaction.verifier).digest("base64url"),
  );
  expect(url.searchParams.get("nonce")).toBe(transaction.nonce);
  expect(url.href).not.toContain(config.clientSecret);
  const code = crypto.randomUUID();
  codes.set(code, {
    nonce: transaction.nonce,
    verifier: transaction.verifier,
    mode,
  });
  return {
    transaction,
    url: new URL(
      config.publicOrigin +
        `/auth/callback?code=${code}&state=${transaction.state}`,
    ),
  };
}

describe("OIDC code + PKCE against real signed HTTP responses", () => {
  it("validates, redeems once and returns only a server-side identity payload", async () => {
    const { transaction, url } = await callback();
    const result = await completeLogin(config, url, transaction);
    expect(result.identity.subject).toBe("subject-one");
    expect(result.tokens.access_token).toBe("private-access");
    await expect(completeLogin(config, url, transaction)).rejects.toThrow();
  });
  it.each(["nonce", "issuer", "audience", "signature", "expiry"])(
    "rejects %s failure",
    async (mode) => {
      const { transaction, url } = await callback(mode);
      const before = tokenResponses;
      await expect(completeLogin(config, url, transaction)).rejects.toThrow();
      expect(tokenResponses).toBe(before + 1);
    },
  );
  it.each(["missing", "wrong", "duplicate", "expired"])(
    "rejects %s state before token exchange",
    async (mode) => {
      const { transaction, url } = await callback();
      const before = exchanges;
      if (mode === "missing") url.searchParams.delete("state");
      if (mode === "wrong") url.searchParams.set("state", "wrong");
      if (mode === "duplicate")
        url.searchParams.append("state", transaction.state);
      if (mode === "expired") transaction.expiresAt = Date.now() - 1;
      await expect(completeLogin(config, url, transaction)).rejects.toThrow();
      expect(exchanges).toBe(before);
    },
  );
});

it("transaction cookie is encrypted, context-bound, tamper-proof and short-lived", async () => {
  const { transaction } = await callback();
  const sealed = sealTransaction(config, transaction);
  expect(sealed).not.toContain(transaction.verifier);
  expect(openTransaction(config, sealed)).toEqual(transaction);
  const bytes = Buffer.from(sealed, "base64url");
  bytes[30] ^= 1;
  expect(() => openTransaction(config, bytes.toString("base64url"))).toThrow();
  expect(() =>
    openTransaction({ ...config, clientId: "different" }, sealed),
  ).toThrow();
  expect(() =>
    openTransaction(config, sealed, transaction.expiresAt),
  ).toThrow();
});
it.each([
  "https://evil.test",
  "//evil.test",
  "/%2f%2fevil.test",
  "/\\evil.test",
  "/auth/callback",
  "/auth/login",
])("rejects unsafe return URL %s", (value) => {
  expect(safeReturnTo(value)).toBe("/");
});
it("requires server secrets and rejects insecure production identity config", () => {
  const values = {
    QTRADE_ENV: "local",
    QTRADE_OIDC_ALLOW_HTTP: "true",
    QTRADE_OIDC_ISSUER: issuer,
    QTRADE_OIDC_CLIENT_ID: "client",
    QTRADE_OIDC_CLIENT_SECRET: "server-secret",
    QTRADE_BFF_SECRET: "b".repeat(43),
    QTRADE_OIDC_TRANSACTION_KEY: Buffer.alloc(32, 7).toString("base64"),
    QTRADE_PUBLIC_ORIGIN: config.publicOrigin,
    QTRADE_API_ORIGIN: config.apiOrigin,
  };
  expect(authConfig(values)?.issuer).toBe(issuer);
  expect(() => authConfig({ ...values, QTRADE_ENV: "production" })).toThrow();
  expect(() =>
    authConfig({ ...values, QTRADE_OIDC_TRANSACTION_KEY: "bad" }),
  ).toThrow();
  expect(() => authConfig({ ...values, QTRADE_BFF_SECRET: "short" })).toThrow();
  expect(authConfig({})).toBeUndefined();
});

describe("signed back-channel logout", () => {
  function claims() {
    const now = Math.floor(Date.now() / 1000);
    return {
      iss: issuer,
      aud: "client",
      sub: "subject-one",
      iat: now,
      exp: now + 120,
      jti: "logout-once",
      events: { [LOGOUT_EVENT]: {} },
    };
  }
  it("accepts provider-compatible subject notices and sid-only notices", async () => {
    expect(await verifyLogout(config, jwt(claims()))).toMatchObject({
      subject: "subject-one",
      jti: "logout-once",
    });
    const base: Record<string, unknown> = claims();
    delete base.sub;
    expect(
      await verifyLogout(config, jwt({ ...base, sid: "provider-session" })),
    ).toMatchObject({ subject: null, sid: "provider-session" });
  });
  it.each([
    "issuer",
    "audience",
    "signature",
    "expiry",
    "future",
    "stale",
    "events",
    "nonce",
    "jti",
    "subject",
    "duration",
  ])("rejects %s before revocation", async (mode) => {
    const value: Record<string, unknown> = claims();
    if (mode === "issuer") value.iss = "https://wrong.test";
    if (mode === "audience") value.aud = "other-client";
    if (mode === "expiry") value.exp = Number(value.iat) - 1;
    if (mode === "future") {
      value.iat = Number(value.iat) + 60;
      value.exp = Number(value.exp) + 60;
    }
    if (mode === "stale") {
      value.iat = Number(value.iat) - 600;
    }
    if (mode === "events") value.events = { other: {} };
    if (mode === "nonce") value.nonce = "forbidden";
    if (mode === "jti") delete value.jti;
    if (mode === "subject") delete value.sub;
    if (mode === "duration") value.exp = Number(value.iat) + 3600;
    await expect(
      verifyLogout(config, jwt(value, mode === "signature")),
    ).rejects.toThrow();
  });
});
