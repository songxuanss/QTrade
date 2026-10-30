import { NextResponse, type NextRequest } from "next/server";
import { navigation } from "./features/workbench/navigation";

export function proxy(request: NextRequest) {
  const path = request.nextUrl.pathname;
  const previewBlocked =
    process.env.NODE_ENV === "production" &&
    (path === "/design-system" || path.startsWith("/design-system/"));
  const unknownSection =
    path.startsWith("/workspace/") &&
    !navigation.some(
      (item) => item.href === path.split("/").slice(0, 3).join("/"),
    );
  if (previewBlocked || unknownSection) {
    // Reject before the root loading boundary streams a 200 response.
    return NextResponse.rewrite(new URL("/_qtrade/missing", request.url), {
      status: 404,
      headers: { "Cache-Control": "no-store" },
    });
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/design-system/:path*", "/workspace/:path*"],
};
