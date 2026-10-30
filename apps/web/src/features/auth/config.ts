export interface AuthConfig {
  issuer: string;
  clientId: string;
  clientSecret: string;
  publicOrigin: string;
  apiOrigin: string;
  publicApiOrigin?: string;
  bridgeSecret: string;
  transactionKey: Buffer;
  allowHttp: boolean;
  secureCookies: boolean;
}

export function authConfig(
  env: Record<string, string | undefined> = process.env,
): AuthConfig | undefined {
  const protectedEnvironment = ["staging", "production"].includes(
    env.QTRADE_ENV ?? "local",
  );
  if (!env.QTRADE_OIDC_ISSUER) {
    if (protectedEnvironment) throw new Error("QTRADE_OIDC_ISSUER is required");
    return undefined;
  }
  const local = ["local", "test"].includes(env.QTRADE_ENV ?? "local");
  const allowHttp = env.QTRADE_OIDC_ALLOW_HTTP === "true";
  if (allowHttp && !local) throw new Error("Invalid identity configuration");
  const required = (name: string) => {
    const value = env[name];
    if (!value) throw new Error("Identity configuration unavailable");
    return value;
  };
  const origin = (value: string, issuer = false, internal = false) => {
    const url = new URL(value);
    if (
      (!internal &&
        url.protocol !== "https:" &&
        !(allowHttp && url.protocol === "http:")) ||
      (internal && !["https:", "http:"].includes(url.protocol)) ||
      url.username ||
      url.password ||
      url.search ||
      url.hash ||
      (!issuer && url.pathname !== "/")
    ) {
      throw new Error("Invalid identity configuration");
    }
    return issuer ? value : url.origin;
  };
  const encoded = required("QTRADE_OIDC_TRANSACTION_KEY");
  const key = Buffer.from(encoded, "base64");
  if (key.length !== 32 || key.toString("base64") !== encoded)
    throw new Error("Invalid identity configuration");
  const bridgeSecret = required("QTRADE_BFF_SECRET");
  if (bridgeSecret.length < 32)
    throw new Error("Invalid identity configuration");
  const insecure = env.QTRADE_INSECURE_LOCAL_COOKIES === "true";
  if (insecure && !local) throw new Error("Invalid identity configuration");
  return {
    issuer: origin(required("QTRADE_OIDC_ISSUER"), true),
    clientId: required("QTRADE_OIDC_CLIENT_ID"),
    clientSecret: required("QTRADE_OIDC_CLIENT_SECRET"),
    publicOrigin: origin(required("QTRADE_PUBLIC_ORIGIN")),
    apiOrigin: origin(required("QTRADE_API_ORIGIN"), false, true),
    publicApiOrigin: env.QTRADE_API_PUBLIC_ORIGIN
      ? origin(env.QTRADE_API_PUBLIC_ORIGIN, false, !protectedEnvironment)
      : undefined,
    bridgeSecret,
    transactionKey: key,
    allowHttp,
    secureCookies: !insecure,
  };
}

export function safeReturnTo(value: string | null): string {
  if (
    !value ||
    !value.startsWith("/") ||
    value.startsWith("//") ||
    /[\\\r\n]/.test(value)
  )
    return "/";
  try {
    const decoded = decodeURIComponent(value);
    if (decoded.startsWith("//") || /[\\\r\n]/.test(decoded)) return "/";
    const url = new URL(value, "https://qtrade.invalid");
    if (
      url.origin !== "https://qtrade.invalid" ||
      url.pathname.startsWith("/auth/")
    )
      return "/";
    return url.pathname + url.search;
  } catch {
    return "/";
  }
}
