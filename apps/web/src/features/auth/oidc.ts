import * as client from "openid-client";
import type { AuthConfig } from "./config";
import { safeReturnTo } from "./config";
import type { LoginTransaction } from "./transaction";

export async function configuration(
  config: AuthConfig,
): Promise<client.Configuration> {
  const discovered = await client.discovery(
    new URL(config.issuer),
    config.clientId,
    { id_token_signed_response_alg: "RS256" },
    client.ClientSecretBasic(config.clientSecret),
    {
      timeout: 10,
      execute: config.allowHttp
        ? [client.allowInsecureRequests, client.enableNonRepudiationChecks]
        : [client.enableNonRepudiationChecks],
    },
  );
  if (discovered.serverMetadata().issuer !== config.issuer)
    throw new Error("Invalid identity issuer");
  return discovered;
}

export async function beginLogin(
  config: AuthConfig,
  returnTo: string | null,
  now = Date.now(),
) {
  const discovered = await configuration(config);
  const transaction: LoginTransaction = {
    state: client.randomState(),
    nonce: client.randomNonce(),
    verifier: client.randomPKCECodeVerifier(),
    returnTo: safeReturnTo(returnTo),
    expiresAt: now + 300_000,
  };
  const challenge = await client.calculatePKCECodeChallenge(
    transaction.verifier,
  );
  const url = client.buildAuthorizationUrl(discovered, {
    redirect_uri: config.publicOrigin + "/auth/callback",
    scope: "openid email profile offline_access",
    response_type: "code",
    code_challenge: challenge,
    code_challenge_method: "S256",
    state: transaction.state,
    nonce: transaction.nonce,
  });
  return { url, transaction };
}

export async function completeLogin(
  config: AuthConfig,
  callback: URL,
  transaction: LoginTransaction,
) {
  const states = callback.searchParams.getAll("state");
  if (
    states.length !== 1 ||
    states[0] !== transaction.state ||
    callback.searchParams.getAll("code").length !== 1 ||
    callback.searchParams.has("error") ||
    transaction.expiresAt <= Date.now()
  )
    throw new Error("Invalid login callback");
  const discovered = await configuration(config);
  const tokens = await client.authorizationCodeGrant(
    discovered,
    new URL(config.publicOrigin + "/auth/callback" + callback.search),
    {
      expectedState: transaction.state,
      expectedNonce: transaction.nonce,
      pkceCodeVerifier: transaction.verifier,
      idTokenExpected: true,
    },
  );
  const claims = tokens.claims();
  if (
    !claims ||
    typeof claims.sub !== "string" ||
    !claims.sub ||
    !tokens.id_token
  )
    throw new Error("Invalid identity token");
  return {
    identity: {
      issuer: claims.iss,
      subject: claims.sub,
      display_name: typeof claims.name === "string" ? claims.name : claims.sub,
      email:
        typeof claims.email === "string" && claims.email_verified === true
          ? claims.email
          : null,
    },
    tokens: {
      access_token: tokens.access_token,
      refresh_token: tokens.refresh_token ?? null,
      id_token: tokens.id_token,
    },
    sso_sid: typeof claims.sid === "string" ? claims.sid : null,
  };
}
