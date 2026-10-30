import { expect, test } from "@playwright/test";

test("browser gets only an HttpOnly opaque session and cannot read SSO tokens", async ({
  page,
}) => {
  await page.goto("http://127.0.0.1:3101/seed");
  const cookies = await page.context().cookies();
  const session = cookies.find((cookie) => cookie.name === "qtrade-session");
  expect(session?.httpOnly).toBe(true);
  expect(session?.value).toMatch(/^[A-Za-z0-9_-]{43}$/);
  expect(session?.sameSite).toBe("Lax");
  expect(await page.evaluate(() => document.cookie)).toBe("");
  expect(
    await page.evaluate(() => [localStorage.length, sessionStorage.length]),
  ).toEqual([0, 0]);
  const response = await page.request.get("http://127.0.0.1:3101/auth/session");
  expect(response.status()).toBe(200);
  expect(response.headers()["cache-control"]).toBe("no-store");
  const context = await response.json();
  const browserContent =
    (await page.content()) + JSON.stringify(context) + JSON.stringify(cookies);
  for (const secret of [
    "browser-private-access",
    "browser-private-refresh",
    "browser-private-id",
  ]) {
    expect(browserContent).not.toContain(secret);
  }
  const forged = await page.request.post("http://127.0.0.1:3101/auth/logout");
  expect(forged.status()).toBe(403);
  expect(
    (await page.request.get("http://127.0.0.1:3101/auth/session")).status(),
  ).toBe(200);
  const logout = await page.request.post("http://127.0.0.1:3101/auth/logout", {
    headers: { "X-CSRF-Token": context.csrf_token },
  });
  expect(logout.status()).toBe(204);
  expect(
    (await page.request.get("http://127.0.0.1:3101/auth/session")).status(),
  ).toBe(401);
  expect(
    (await page.context().cookies()).some(
      (cookie) => cookie.name === "qtrade-session",
    ),
  ).toBe(false);
});
