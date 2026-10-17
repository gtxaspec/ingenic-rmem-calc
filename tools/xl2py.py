#!/usr/bin/env python3
"""Translate one calculator sheet's formulas into a Python module.

    xl2py.py <models.tsv> <inventory_dir> <out_dir> [model...]

Every formula cell becomes one assignment, in dependency order, commented
with the sheet's own row/column labels; inputs keep the sheet's defaults and
dropdown options. The generated module is the reference model the vectors are
checked against, so it is regenerated rather than edited.
"""
import json
import os
import re
import sys

TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<string>"(?:[^"]|"")*")
  | (?P<number>\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)
  | (?P<ref>\$?[A-Z]{1,3}\$?\d+(?::\$?[A-Z]{1,3}\$?\d+)?)(?![A-Za-z0-9_(])
  | (?P<func>(?:_xlfn\.|_xlws\.)?[A-Z][A-Z0-9.]*)(?=\()
  | (?P<bool>TRUE|FALSE)(?![A-Za-z0-9_(])
  | (?P<op><>|<=|>=|[-+*/^&=<>%(),])
""", re.X)

CMP = ("=", "<>", "<", ">", "<=", ">=")


def tokenize(src):
    pos, out = 0, []
    while pos < len(src):
        m = TOKEN.match(src, pos)
        if not m:
            raise SyntaxError(f"cannot tokenize {src[pos:pos + 20]!r} in {src!r}")
        pos = m.end()
        kind = m.lastgroup
        if kind != "ws":
            out.append((kind, m.group(kind)))
    out.append(("end", None))
    return out


def col_num(col):
    n = 0
    for ch in col:
        n = n * 26 + ord(ch) - 64
    return n


def col_name(n):
    s = ""
    while n:
        n, rem = divmod(n - 1, 26)
        s = chr(65 + rem) + s
    return s


def expand(ref):
    ref = ref.replace("$", "")
    if ":" not in ref:
        return [ref]
    a, b = ref.split(":")
    ma, mb = re.fullmatch(r"([A-Z]+)(\d+)", a), re.fullmatch(r"([A-Z]+)(\d+)", b)
    c1, r1, c2, r2 = col_num(ma.group(1)), int(ma.group(2)), col_num(mb.group(1)), int(mb.group(2))
    return [f"{col_name(c)}{r}" for r in range(min(r1, r2), max(r1, r2) + 1)
            for c in range(min(c1, c2), max(c1, c2) + 1)]


class Parser:
    """Excel precedence: comparison < & < + - < * / < ^ < unary < %."""

    def __init__(self, src):
        self.toks = tokenize(src)
        self.i = 0
        self.refs = set()

    def peek(self):
        return self.toks[self.i]

    def take(self, value=None):
        tok = self.toks[self.i]
        if value is not None and tok[1] != value:
            raise SyntaxError(f"expected {value!r}, got {tok!r}")
        self.i += 1
        return tok

    def parse(self):
        expr = self.comparison()
        if self.peek()[0] != "end":
            raise SyntaxError(f"trailing {self.peek()!r}")
        return expr

    def comparison(self):
        left = self.concat()
        while self.peek()[0] == "op" and self.peek()[1] in CMP:
            op = self.take()[1]
            left = f"xl.compare({left}, {self.concat()}, {op!r})"
        return left

    def concat(self):
        left = self.additive()
        while self.peek() == ("op", "&"):
            self.take()
            left = f"xl.concat({left}, {self.additive()})"
        return left

    def additive(self):
        left = self.multiplicative()
        while self.peek()[0] == "op" and self.peek()[1] in "+-":
            fn = "add" if self.take()[1] == "+" else "sub"
            left = f"xl.{fn}({left}, {self.multiplicative()})"
        return left

    def multiplicative(self):
        left = self.power()
        while self.peek()[0] == "op" and self.peek()[1] in "*/":
            fn = "mul" if self.take()[1] == "*" else "div"
            left = f"xl.{fn}({left}, {self.power()})"
        return left

    def power(self):
        left = self.unary()
        if self.peek() == ("op", "^"):
            raise SyntaxError("'^' is not used by any calculator sheet")
        return left

    def unary(self):
        if self.peek() == ("op", "-"):
            self.take()
            return f"xl.neg({self.unary()})"
        if self.peek() == ("op", "+"):
            self.take()
            return self.unary()
        return self.percent()

    def percent(self):
        expr = self.primary()
        while self.peek() == ("op", "%"):
            self.take()
            expr = f"xl.div({expr}, 100.0)"
        return expr

    def primary(self):
        kind, val = self.take()
        if kind == "number":
            return repr(float(val))
        if kind == "string":
            return repr(val[1:-1].replace('""', '"'))
        if kind == "bool":
            return "True" if val == "TRUE" else "False"
        if kind == "ref":
            cells = expand(val)
            self.refs.update(cells)
            return cells[0] if len(cells) == 1 and ":" not in val else "[" + ", ".join(cells) + "]"
        if kind == "func":
            # files store post-2007 functions as _xlfn.NAME
            return self.call(re.sub(r"^_xl(fn|ws)\.", "", val))
        if (kind, val) == ("op", "("):
            expr = self.comparison()
            self.take(")")
            return f"({expr})"
        raise SyntaxError(f"unexpected {val!r}")

    def call(self, name):
        self.take("(")
        args = []
        if self.peek() != ("op", ")"):
            while True:
                if self.peek() in (("op", ","), ("op", ")")):
                    args.append("xl.EMPTY")
                else:
                    args.append(self.comparison())
                if self.peek() == ("op", ","):
                    self.take()
                    continue
                break
        self.take(")")
        if name == "IF":
            # only the taken branch is evaluated, as in Excel
            cond, then = args[0], args[1] if len(args) > 1 else "False"
            other = args[2] if len(args) > 2 else "False"
            return f"({then} if xl.truth({cond}) else {other})"
        if name == "IFS":
            expr = "xl.na()"
            for c, v in reversed(list(zip(args[0::2], args[1::2]))):
                expr = f"({v} if xl.truth({c}) else {expr})"
            return expr
        known = {"CEILING", "ROUNDDOWN", "ROUNDUP", "HEX2DEC", "DEC2HEX", "INT", "AND", "OR", "MOD", "SUM", "MAX"}
        if name not in known:
            raise SyntaxError(f"function {name} not implemented")
        return f"xl.{name}({', '.join(args)})"


def label(lbl):
    parts = [p for p in (lbl.get("row"), lbl.get("col")) if p]
    return " / ".join(parts).replace("\n", " ")


def generate(model, book, sheet_name, sh, socs):
    formulas = sh["formulas"]
    exprs, deps = {}, {}
    for ref, f in formulas.items():
        p = Parser(f["formula"])
        exprs[ref] = p.parse()
        deps[ref] = p.refs
    order, state = [], {}

    def visit(ref, stack=()):
        if state.get(ref) == 2:
            return
        if state.get(ref) == 1:
            raise SystemExit(f"{model}: circular reference through {' -> '.join(stack + (ref,))}")
        state[ref] = 1
        for d in sorted(deps[ref]):
            if d in formulas:
                visit(d, stack + (ref,))
        state[ref] = 2
        order.append(ref)
    for ref in formulas:
        visit(ref)

    referenced = set().union(*deps.values()) if deps else set()
    inputs = sh["inputs"]
    blanks = sorted(r for r in referenced if r not in formulas and r not in inputs)

    overrides = {ref for ref, info in inputs.items() if info.get("override")}
    out = [f'"""{model}: {socs}. Generated by tools/xl2py.py from',
           f"{book['workbook']} sheet {sheet_name.strip()!r}; regenerate, do not edit.",
           '"""', "from .. import xl", "", "SOURCE = " + repr({"workbook": book["workbook"], "sheet": sheet_name}),
           "", "# input cell -> saved default, dropdown options, sheet labels; an 'override'",
           "# cell is a formula the user may replace by picking a value (default None = computed)",
           "INPUTS = {"]
    for ref, info in inputs.items():
        default = None if ref in overrides else info.get("value")
        out.append(f"    {ref!r}: {{'default': {default!r}, 'options': {info.get('options')!r}, "
                   f"'override': {ref in overrides!r}, 'validation': {info.get('validation')!r}, "
                   f"'label': {label(info['label'])!r}}},")
    out += ["}", "", "# formula cell -> sheet labels", "LABELS = {"]
    for ref in order:
        out.append(f"    {ref!r}: {label(formulas[ref]['label'])!r},")
    out += ["}", "", "", "def compute(values=None):",
            '    """All formula cells for the given input cell values (defaults fill the rest).',
            "    Error cells hold an xl.XLError, like an Excel error value.",
            '    """',
            "    v = {ref: info['default'] for ref, info in INPUTS.items()}",
            "    v.update(values or {})",
            "    out = {}"]
    for ref in inputs:
        if ref not in overrides:
            out.append(f"    {ref} = v[{ref!r}]")
    for ref in blanks:
        out.append(f"    {ref} = xl.EMPTY")
    for ref in order:
        out.append(f"    # {ref}: {label(formulas[ref]['label'])}  ={formulas[ref]['formula'][:150]}")
        pad = "    "
        if ref in overrides:
            out += [f"    if v[{ref!r}] is not None:",
                    f"        {ref} = v[{ref!r}]",
                    f"    else:"]
            pad = "        "
        out += [f"{pad}try:",
                f"{pad}    {ref} = {exprs[ref]}",
                f"{pad}except xl.XLError as e:",
                f"{pad}    {ref} = e"]
        out.append(f"    out[{ref!r}] = {ref}")
    out += ["    return out", ""]
    return "\n".join(out)


def main():
    models_tsv, inv_dir, out_dir = sys.argv[1:4]
    only = set(sys.argv[4:])
    os.makedirs(out_dir, exist_ok=True)
    init = os.path.join(out_dir, "__init__.py")
    if not os.path.exists(init):
        open(init, "w").write('"""Generated calculator models, one per vendor sheet."""\n')
    for row in open(models_tsv, encoding="utf-8"):
        if not row.strip() or row.startswith("#"):
            continue
        model, inv, sheet_name, socs = row.rstrip("\n").split("\t")
        if only and model not in only:
            continue
        book = json.load(open(os.path.join(inv_dir, inv), encoding="utf-8"))
        sh = book["sheets"][sheet_name]
        src = generate(model, book, sheet_name, sh, socs)
        with open(os.path.join(out_dir, f"{model}.py"), "w", encoding="utf-8") as fh:
            fh.write(src)
        print(f"{model}: {len(sh['formulas'])} formulas, {len(sh['inputs'])} inputs")


if __name__ == "__main__":
    main()
