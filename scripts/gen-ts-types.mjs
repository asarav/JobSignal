#!/usr/bin/env node
/**
 * Generate frontend/src/types/generated.ts from schemas/ipc/**\/*.json.
 *
 * Usage: node scripts/gen-ts-types.mjs [--check]
 * Requires frontend dependencies to be installed (json-schema-to-typescript).
 */
import { createRequire } from "node:module";
import { readFileSync, readdirSync, statSync, writeFileSync, existsSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const schemaRoot = join(root, "schemas", "ipc");
const output = join(root, "frontend", "src", "types", "generated.ts");
const requireFromFrontend = createRequire(join(root, "frontend", "package.json"));
const { compile } = requireFromFrontend("json-schema-to-typescript");

function walk(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else if (entry.endsWith(".json") && entry !== "methods.json") out.push(full);
  }
  return out.sort();
}

const HEADER = `// AUTO-GENERATED from schemas/ipc/**/*.json. DO NOT EDIT BY HAND.
// Regenerate with: node scripts/gen-ts-types.mjs
// Source of truth: Pydantic models in app/worker/ipc/method_catalog.py
// CI fails if this file is stale.

`;

async function build() {
  const parts = [];
  for (const file of walk(schemaRoot)) {
    const schema = JSON.parse(readFileSync(file, "utf8"));
    const name = schema.title || relative(schemaRoot, file).replace(/[^A-Za-z0-9]/g, "_");
    const ts = await compile(schema, name, {
      bannerComment: "",
      additionalProperties: false,
      declareExternallyReferenced: true,
      format: true,
    });
    parts.push(`// ${relative(root, file).replaceAll("\\", "/")}\n${ts.trim()}`);
  }
  return HEADER + parts.join("\n\n") + "\n";
}

const check = process.argv.includes("--check");
const content = await build();
if (check) {
  const current = existsSync(output) ? readFileSync(output, "utf8") : "";
  if (current !== content) {
    console.error(`Stale generated types: ${relative(root, output)}`);
    console.error("Run: node scripts/gen-ts-types.mjs");
    process.exit(1);
  }
  console.log("Generated TypeScript types are up to date.");
} else {
  writeFileSync(output, content, "utf8");
  console.log(`Wrote ${relative(root, output)}`);
}
