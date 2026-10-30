import { cpSync } from "node:fs";

// Match Docker's standalone packaging: Next does not copy these assets itself.
cpSync(".next/static", ".next/standalone/.next/static", { recursive: true });
cpSync("public", ".next/standalone/public", { recursive: true });
await import("../.next/standalone/server.js");
