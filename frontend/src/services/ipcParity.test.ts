import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import * as ts from "typescript";
import { describe, expect, it } from "vitest";

const frontendRoot = process.cwd().endsWith("frontend")
  ? process.cwd()
  : join(process.cwd(), "frontend");
const root = join(frontendRoot, "..");
const src = join(frontendRoot, "src");

function parse(file: string): ts.SourceFile {
  return ts.createSourceFile(
    file,
    readFileSync(file, "utf8"),
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TSX,
  );
}

function workerSurface() {
  const client = parse(join(src, "services", "workerClient.ts"));
  const wrappers = new Map<string, { requestType: string | null; ipc: string | null }>();
  const methods = new Set<string>();
  for (const statement of client.statements) {
    if (
      ts.isTypeAliasDeclaration(statement) &&
      statement.name.text === "WorkerMethod" &&
      ts.isUnionTypeNode(statement.type)
    ) {
      for (const member of statement.type.types) {
        if (ts.isLiteralTypeNode(member) && ts.isStringLiteral(member.literal)) {
          methods.add(member.literal.text);
        }
      }
    }
    if (ts.isClassDeclaration(statement) && statement.name?.text === "WorkerClient") {
      for (const member of statement.members) {
        if (
          !ts.isMethodDeclaration(member) ||
          !member.name ||
          !ts.isIdentifier(member.name)
        ) {
          continue;
        }
        const parameter = member.parameters[0];
        const requestType =
          parameter?.type && ts.isTypeReferenceNode(parameter.type)
            ? parameter.type.typeName.getText()
            : null;
        let ipc: string | null = null;
        const visit = (node: ts.Node) => {
          if (
            ts.isCallExpression(node) &&
            node.expression.getText() === "this.request" &&
            node.arguments[0] &&
            ts.isStringLiteral(node.arguments[0])
          ) {
            ipc = node.arguments[0].text;
          }
          ts.forEachChild(node, visit);
        };
        if (member.body) visit(member.body);
        wrappers.set(member.name.text, { requestType, ipc });
      }
    }
  }
  return { wrappers, methods };
}

function generatedInterfaces() {
  const generated = parse(join(src, "types", "generated.ts"));
  const interfaces = new Map<string, Set<string>>();
  for (const statement of generated.statements) {
    if (!ts.isInterfaceDeclaration(statement)) continue;
    const fields = new Set<string>();
    for (const member of statement.members) {
      if (member.name && ts.isIdentifier(member.name)) fields.add(member.name.text);
    }
    interfaces.set(statement.name.text, fields);
  }
  return interfaces;
}

function uiCalls() {
  const calls: Array<{ file: string; line: number; wrapper: string; keys: string[] }> = [];
  const walk = (directory: string) => {
    for (const entry of readdirSync(directory)) {
      const full = join(directory, entry);
      if (statSync(full).isDirectory()) {
        walk(full);
        continue;
      }
      if (
        !/\.(ts|tsx)$/.test(entry) ||
        /\.test\.(ts|tsx)$/.test(entry) ||
        entry === "workerClient.ts"
      ) {
        continue;
      }
      const file = parse(full);
      const visit = (node: ts.Node) => {
        if (
          ts.isCallExpression(node) &&
          ts.isPropertyAccessExpression(node.expression) &&
          node.expression.expression.getText() === "workerClient"
        ) {
          const argument = node.arguments[0];
          const keys: string[] = [];
          if (argument && ts.isObjectLiteralExpression(argument)) {
            for (const property of argument.properties) {
              if (
                ts.isPropertyAssignment(property) ||
                ts.isShorthandPropertyAssignment(property)
              ) {
                keys.push(property.name.getText());
              }
            }
          }
          calls.push({
            file: relative(root, full).replaceAll("\\", "/"),
            line: file.getLineAndCharacterOfPosition(node.getStart()).line + 1,
            wrapper: node.expression.name.text,
            keys,
          });
        }
        ts.forEachChild(node, visit);
      };
      visit(file);
    }
  };
  walk(src);
  return calls;
}

function previewHandlers(): string[] {
  const bridge = parse(join(src, "services", "workerBridge.ts"));
  const handlers: string[] = [];
  const visit = (node: ts.Node) => {
    if (ts.isCaseClause(node) && ts.isStringLiteral(node.expression)) {
      handlers.push(node.expression.text);
    }
    ts.forEachChild(node, visit);
  };
  visit(bridge);
  return handlers;
}

describe("IPC parity", () => {
  const schemas = JSON.parse(
    readFileSync(join(root, "schemas", "ipc", "methods.json"), "utf8"),
  ) as { methods: Array<{ method: string }> };
  const schemaMethods = new Set(schemas.methods.map((entry) => entry.method));
  const { wrappers, methods } = workerSurface();
  const interfaces = generatedInterfaces();
  const calls = uiCalls();
  const preview = previewHandlers();

  it("exposes exactly the methods the real worker catalogues", () => {
    expect([...methods].sort()).toEqual([...schemaMethods].sort());
  });

  it("maps every client wrapper to a registered IPC method", () => {
    const missing: string[] = [];
    for (const [name, wrapper] of wrappers) {
      if (name === "request" || name === "onEvent") continue;
      if (!wrapper.ipc || !schemaMethods.has(wrapper.ipc)) {
        missing.push(`${name} -> ${wrapper.ipc ?? "(none)"}`);
      }
    }
    expect(missing).toEqual([]);
  });

  it("sends only request fields the real schema accepts", () => {
    const invalid: string[] = [];
    for (const call of calls) {
      if (call.wrapper === "onEvent") continue;
      const wrapper = wrappers.get(call.wrapper);
      if (!wrapper) {
        invalid.push(`${call.file}:${call.line} unknown wrapper ${call.wrapper}`);
        continue;
      }
      const fields = wrapper.requestType ? interfaces.get(wrapper.requestType) : undefined;
      if (!fields) {
        invalid.push(
          `${call.file}:${call.line} ${call.wrapper} missing interface ${wrapper.requestType}`,
        );
        continue;
      }
      for (const key of call.keys) {
        if (!fields.has(key)) {
          invalid.push(
            `${call.file}:${call.line} ${call.wrapper} -> ${wrapper.ipc}: ${key} not in ${wrapper.requestType}`,
          );
        }
      }
    }
    expect(invalid).toEqual([]);
  });

  it("implements only preview methods that exist in the real worker", () => {
    expect([...new Set(preview)].filter((method) => !schemaMethods.has(method))).toEqual([]);
  });

  it("covers every workflow method in both the real catalogue and preview", () => {
    const workflow = [
      "crawl_runs.list",
      "sources.list",
      "jobs.ranked",
      "jobs.list",
      "jobs.list_saved",
      "jobs.get_detail",
      "jobs.set_user_state",
      "search_profiles.list",
      "search_profiles.save",
      "search_profiles.delete",
      "resume.import",
      "resume.list_versions",
      "resume.get_profile",
      "matching.run",
      "applications.list",
      "applications.create",
      "applications.update_status",
      "applications.record_outcome",
      "applications.get_analytics",
      "applications.get_suppressions",
      "applications.get_suppression_policy",
      "companies.list",
      "companies.get_detail",
      "companies.get_merge_history",
      "companies.merge",
      "companies.unmerge",
    ];
    expect(workflow.filter((method) => !schemaMethods.has(method))).toEqual([]);
    expect(workflow.filter((method) => !preview.includes(method))).toEqual([]);
  });

  it("scans real call sites rather than trusting an empty fixture", () => {
    expect(calls.length).toBeGreaterThan(100);
    expect(calls.some((call) => call.wrapper === "listCrawlRuns")).toBe(true);
  });
});
