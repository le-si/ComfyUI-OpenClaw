import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const EXTENSION_ENTRY = path.resolve(process.cwd(), "web", "openclaw.js");

// A boot diagnostic guards on an identifier and warns when it is missing. If that identifier
// is never bound in the module, `typeof` evaluates to "undefined" without throwing and the
// guard is unconditionally true: the warning fires on every load of every installation and
// reports a fault that does not exist. That failure is invisible to a runtime test, because
// the module is only ever exercised through the host's extension registration, so it is
// checked statically here instead.
const TYPEOF_GUARD = /typeof\s+([A-Za-z_$][\w$]*)\s*!==\s*["']function["']/g;
const TRUTHY_GUARD = /if\s*\(\s*!\s*([A-Za-z_$][\w$]*)\s*\)/g;

const NAMED_IMPORT_BLOCK = /import\s*\{([^}]*)\}\s*from/g;
const DEFAULT_OR_NAMESPACE_IMPORT =
    /import\s+(?:\*\s+as\s+)?([A-Za-z_$][\w$]*)\s*(?:,|from)/g;
const DECLARATION = /\b(?:const|let|var|function|class)\s+([A-Za-z_$][\w$]*)/g;
const PARAMETER_LIST = /(?:function\s*[\w$]*\s*|\)\s*=>|\b[\w$]+\s*)\(([^)]*)\)/g;

function collectMatches(source, pattern) {
    const found = [];
    for (const match of source.matchAll(pattern)) {
        found.push(match[1]);
    }
    return found;
}

function boundIdentifiers(source) {
    const bound = new Set(["window", "document", "console", "globalThis"]);

    for (const block of collectMatches(source, NAMED_IMPORT_BLOCK)) {
        for (const specifier of block.split(",")) {
            const name = specifier.split(/\s+as\s+/).pop().trim();
            if (name) bound.add(name);
        }
    }
    for (const name of collectMatches(source, DEFAULT_OR_NAMESPACE_IMPORT)) {
        bound.add(name);
    }
    for (const name of collectMatches(source, DECLARATION)) {
        bound.add(name);
    }
    for (const list of collectMatches(source, PARAMETER_LIST)) {
        for (const parameter of list.split(",")) {
            const name = parameter.split(/[=:]/)[0].trim().replace(/^\.\.\./, "");
            if (/^[A-Za-z_$][\w$]*$/.test(name)) bound.add(name);
        }
    }
    return bound;
}

function unboundGuardIdentifiers(source) {
    const bound = boundIdentifiers(source);
    const guarded = new Set([
        ...collectMatches(source, TYPEOF_GUARD),
        ...collectMatches(source, TRUTHY_GUARD),
    ]);
    return [...guarded].filter((name) => !bound.has(name)).sort();
}

describe("extension boot diagnostics", () => {
    it("guards only on identifiers the entry module actually binds", () => {
        const source = readFileSync(EXTENSION_ENTRY, "utf8");

        expect(unboundGuardIdentifiers(source)).toEqual([]);
    });

    it("reports an unbound guard, so a clean result means something", () => {
        // Without this the check could pass by finding nothing at all, which is the failure
        // mode of a diagnostic that cannot fail - exactly what it exists to prevent.
        const fixture = [
            'import { app } from "../../scripts/app.js";',
            "async function setup() {",
            '    if (typeof missingShim !== "function") {',
            '        console.warn("missing");',
            "    }",
            "    if (!app) {",
            '        console.warn("no app");',
            "    }",
            "}",
        ].join("\n");

        expect(unboundGuardIdentifiers(fixture)).toEqual(["missingShim"]);
    });
});
