import { cp, mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const appRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const source = path.join(appRoot, "node_modules/monaco-editor/min/vs");
const target = path.join(appRoot, "public/monaco/vs");

await mkdir(path.dirname(target), { recursive: true });
await cp(source, target, { recursive: true, force: true });
