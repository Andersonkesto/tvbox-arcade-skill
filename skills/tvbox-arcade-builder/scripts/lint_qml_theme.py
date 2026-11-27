"""Lint a Pegasus theme for JavaScript syntax that Qt 5.15 (Pegasus Android) rejects.

Qt 5.15's QJSEngine implements ES7-ish JavaScript. Themes written for newer Qt
often use ES2020/ES2021 operators that make Pegasus fail with a QML
`Syntax error` and silently fall back to the default theme:

  ??   nullish coalescing          ?.   optional chaining
  ??=  ||=  &&=  logical assignment

Comments and string/template literals are ignored, and `cond ?.5 : 1`
(ternary followed by a decimal) is not reported as optional chaining.

Exit code: 0 = clean, 1 = incompatible syntax found, 2 = usage error.
"""
import argparse
import json
import os
import re
import sys

EXTENSIONS = (".qml", ".js", ".mjs")

RULES = [
    (re.compile(r"\?\?="), "??=", "logical nullish assignment (ES2021)"),
    (re.compile(r"\|\|="), "||=", "logical OR assignment (ES2021)"),
    (re.compile(r"&&="), "&&=", "logical AND assignment (ES2021)"),
    (re.compile(r"\?\?(?!=)"), "??", "nullish coalescing (ES2020)"),
    (re.compile(r"\?\.(?!\d)"), "?.", "optional chaining (ES2020)"),
]

FIXES = {
    "??": "use `(a !== undefined && a !== null) ? a : b`",
    "?.": "use `a && a.b`",
    "??=": "use `if (a === undefined || a === null) a = b`",
    "||=": "use `if (!a) a = b`",
    "&&=": "use `if (a) a = b`",
}


def strip_comments_and_strings(src: str) -> str:
    """Blank out comments and string/template literals, keeping offsets and newlines intact."""
    out = list(src)
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            j = src.find("\n", i)
            j = n if j == -1 else j
            for k in range(i, j):
                out[k] = " "
            i = j
        elif c == "/" and nxt == "*":
            j = src.find("*/", i + 2)
            j = n if j == -1 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        elif c in ("'", '"', "`"):
            quote = c
            j = i + 1
            while j < n and src[j] != quote:
                if src[j] == "\\":
                    j += 1
                elif src[j] == "\n" and quote != "`":
                    break
                j += 1
            end = min(j + 1, n)
            for k in range(i + 1, end - 1):
                if out[k] != "\n":
                    out[k] = " "
            i = end
        else:
            i += 1
    return "".join(out)


def lint_source(src: str) -> list[dict]:
    clean = strip_comments_and_strings(src)
    findings: list[dict] = []
    taken: set[int] = set()
    lines = src.splitlines()
    for rx, op, desc in RULES:
        for m in rx.finditer(clean):
            if m.start() in taken:
                continue
            taken.update(range(m.start(), m.end()))
            line = clean.count("\n", 0, m.start()) + 1
            col = m.start() - (clean.rfind("\n", 0, m.start()) + 1) + 1
            findings.append({
                "line": line, "col": col, "operator": op, "message": desc,
                "fix": FIXES[op], "code": lines[line - 1].strip() if line - 1 < len(lines) else "",
            })
    return sorted(findings, key=lambda f: (f["line"], f["col"]))


def iter_theme_files(path: str):
    if os.path.isfile(path):
        yield path
        return
    for root, _, files in os.walk(path):
        for fn in sorted(files):
            if fn.lower().endswith(EXTENSIONS):
                yield os.path.join(root, fn)


def main():
    parser = argparse.ArgumentParser(description="Find ES2020+ syntax that breaks Pegasus (Qt 5.15) themes.")
    parser.add_argument("paths", nargs="+", help="Theme directories or .qml/.js files")
    parser.add_argument("--json", action="store_true", help="Output findings as JSON")
    args = parser.parse_args()

    report: dict[str, list[dict]] = {}
    scanned = 0
    for p in args.paths:
        if not os.path.exists(p):
            print(f"ERROR: path not found: {p}", file=sys.stderr)
            sys.exit(2)
        for f in iter_theme_files(p):
            scanned += 1
            with open(f, "r", encoding="utf-8", errors="replace") as fh:
                found = lint_source(fh.read())
            if found:
                report[f] = found

    total = sum(len(v) for v in report.values())
    if args.json:
        print(json.dumps({"scanned": scanned, "issues": total, "files": report}, indent=2, ensure_ascii=False))
    else:
        for f, items in report.items():
            for it in items:
                print(f"{f}:{it['line']}:{it['col']}: `{it['operator']}` {it['message']} -> {it['fix']}")
                print(f"    {it['code']}")
        print(f"Scanned {scanned} file(s): {total} Qt 5.15-incompatible construct(s).")
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main()
