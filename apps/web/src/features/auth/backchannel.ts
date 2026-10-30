import { createRemoteJWKSet, jwtVerify } from "jose";
import type { AuthConfig } from "./config";
import { configuration } from "./oidc";

export const LOGOUT_EVENT =
  "http://schemas.openid.net/event/backchannel-logout";

export async function verifyLogout(config: AuthConfig, token: string) {
  if (!token || token.length > 65_536) throw Error("Invalid logout token");
  const discovered = await configuration(config);
  const uri = discovered.serverMetadata().jwks_uri;
  if (!uri) throw Error("Identity keys unavailable");
  const keys = new URL(uri);
  if (
    keys.protocol !== "https:" &&
    !(config.allowHttp && keys.protocol === "http:")
  )
    throw Error("Invalid identity keys");
  const { payload } = await jwtVerify(
    token,
    createRemoteJWKSet(keys, { timeoutDuration: 10_000 }),
    {
      issuer: config.issuer,
      audience: config.clientId,
      algorithms: ["RS256"],
      requiredClaims: ["iss", "aud", "iat", "exp", "jti", "events"],
      maxTokenAge: "5m",
      clockTolerance: 0,
    },
  );
  const events = payload.events;
  const validString = (value: unknown): value is string =>
    typeof value === "string" && value.length > 0 && value.length <= 256;
  if (
    !validString(payload.jti) ||
    "nonce" in payload ||
    !events ||
    typeof events !== "object" ||
    Array.isArray(events) ||
    Object.keys(events).length !== 1 ||
    !(LOGOUT_EVENT in events) ||
    events[LOGOUT_EVENT] === null ||
    typeof events[LOGOUT_EVENT] !== "object" ||
    Array.isArray(events[LOGOUT_EVENT]) ||
    Object.keys(events[LOGOUT_EVENT]).length !== 0 ||
    (!validString(payload.sub) && !validString(payload.sid)) ||
    ("sub" in payload && !validString(payload.sub)) ||
    ("sid" in payload && !validString(payload.sid)) ||
    typeof payload.exp !== "number" ||
    typeof payload.iat !== "number" ||
    payload.exp <= payload.iat ||
    payload.exp - payload.iat > 300
  )
    throw Error("Invalid logout claims");
  return {
    issuer: config.issuer,
    jti: payload.jti,
    expires_at: new Date(payload.exp * 1000).toISOString(),
    subject: payload.sub ?? null,
    sid: typeof payload.sid === "string" ? payload.sid : null,
  };
}
