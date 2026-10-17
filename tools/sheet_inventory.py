#!/usr/bin/env python3
"""Inventory a vendor rmem calculator workbook: inputs, options, formulas, notes.

    sheet_inventory.py <workbook.xlsx> <out.json>

Reads the OOXML parts with the standard library only; nothing in the workbook
is executed. For every sheet it records each formula cell (shared formulas
expanded to their per-cell text), each input cell (a constant that a formula
reads, or a cell with a dropdown), the dropdown options, cell comments and the
cached values the authoring application saved. The JSON is the checklist the
port is measured against.
"""
import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

M = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
X14 = "http://schemas.microsoft.com/office/spreadsheetml/2009/9/main"
XM = "http://schemas.microsoft.com/office/excel/2006/main"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"

REF = re.compile(
    r"(?<![A-Za-z0-9_.\]])"
    r"(?:(?P<sheet>'(?:[^']|'')+'|[^\s'!(),=<>&+\-*/^\"]+)!)?"
    r"(?P<c1>\$?[A-Z]{1,3})(?P<r1>\$?\d+)"
    r"(?::(?P<c2>\$?[A-Z]{1,3})(?P<r2>\$?\d+))?"
    r"(?![A-Za-z0-9_(])")
STRING = re.compile(r'"(?:[^"]|"")*"')


def col_num(col):
    n = 0
    for ch in col.lstrip("$"):
        n = n * 26 + ord(ch) - 64
    return n


def col_name(n):
    s = ""
    while n:
        n, rem = divmod(n - 1, 26)
        s = chr(65 + rem) + s
    return s


def split_ref(ref):
    m = re.fullmatch(r"([A-Z]{1,3})(\d+)", ref)
    return col_num(m.group(1)), int(m.group(2))


def outside_strings(formula, fn):
    """Apply fn to the parts of formula that are not string literals."""
    out, pos = [], 0
    for m in STRING.finditer(formula):
        out.append(fn(formula[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(fn(formula[pos:]))
    return "".join(out)


def shift_formula(formula, dcol, drow):
    """Translate relative references the way Excel fills a shared formula."""
    def shift_part(col, row):
        if not col.startswith("$"):
            col = col_name(col_num(col) + dcol)
        if not row.startswith("$"):
            row = str(int(row) + drow)
        return col + row

    def fix(text):
        def rep(m):
            s = (m.group("sheet") + "!") if m.group("sheet") else ""
            first = shift_part(m.group("c1"), m.group("r1"))
            if m.group("c2"):
                return f"{s}{first}:{shift_part(m.group('c2'), m.group('r2'))}"
            return s + first
        return REF.sub(rep, text)
    return outside_strings(formula, fix)


def refs_in(formula, own_sheet):
    """Every (sheet, A1) cell a formula reads, ranges expanded."""
    found = []

    def scan(text):
        for m in REF.finditer(text):
            sheet = m.group("sheet")
            sheet = sheet.strip("'").replace("''", "'") if sheet else own_sheet
            c1, r1 = col_num(m.group("c1")), int(m.group("r1").lstrip("$"))
            if m.group("c2"):
                c2, r2 = col_num(m.group("c2")), int(m.group("r2").lstrip("$"))
                for c in range(min(c1, c2), max(c1, c2) + 1):
                    for r in range(min(r1, r2), max(r1, r2) + 1):
                        found.append((sheet, f"{col_name(c)}{r}"))
            else:
                found.append((sheet, f"{col_name(c1)}{r1}"))
        return text
    outside_strings(formula, scan)
    return found


def expand_sqref(sqref):
    cells = []
    for part in sqref.split():
        if ":" in part:
            a, b = part.split(":")
            (c1, r1), (c2, r2) = split_ref(a.replace("$", "")), split_ref(b.replace("$", ""))
            cells += [f"{col_name(c)}{r}" for r in range(r1, r2 + 1) for c in range(c1, c2 + 1)]
        else:
            cells.append(part.replace("$", ""))
    return cells


def text_of(node):
    return "".join(t.text or "" for t in node.iter(f"{{{M}}}t"))


def main(path, out_path):
    z = zipfile.ZipFile(path)
    names = set(z.namelist())
    shared = []
    if "xl/sharedStrings.xml" in names:
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(f"{{{M}}}si"):
            shared.append(text_of(si))

    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    defined = {}
    dn = wb.find(f"{{{M}}}definedNames")
    if dn is not None:
        for d in dn:
            defined[d.get("name")] = d.text

    sheets = []
    for sh in wb.find(f"{{{M}}}sheets"):
        target = rels[sh.get(f"{{{R}}}id")].lstrip("/")
        part = target if target.startswith("xl/") else "xl/" + target
        sheets.append((sh.get("name"), part, sh.get("state", "visible")))

    book = {"workbook": path.split("/")[-1], "defined_names": defined, "sheets": {}}
    cells_by_sheet = {}
    for name, part, state in sheets:
        root = ET.fromstring(z.read(part))
        masters, cells = {}, {}
        for c in root.iter(f"{{{M}}}c"):
            ref, typ = c.get("r"), c.get("t")
            v, f, isel = c.find(f"{{{M}}}v"), c.find(f"{{{M}}}f"), c.find(f"{{{M}}}is")
            if typ == "s" and v is not None:
                value = shared[int(v.text)]
            elif typ == "inlineStr" and isel is not None:
                value = text_of(isel)
            elif typ == "b" and v is not None:
                value = v.text == "1"
            elif v is not None and v.text is not None:
                value = v.text
                if typ not in ("str", "e"):
                    try:
                        value = float(value)
                        if value.is_integer():
                            value = int(value)
                    except ValueError:
                        pass
            else:
                value = None
            formula = None
            if f is not None:
                ftype = f.get("t")
                if ftype == "shared":
                    si = f.get("si")
                    if f.text:
                        masters[si] = (ref, f.text)
                        formula = f.text
                    else:
                        formula = ("shared", si)
                else:
                    formula = f.text or ""
            cells[ref] = {"value": value, "type": typ or "n", "formula": formula}
        for ref, cell in cells.items():
            if isinstance(cell["formula"], tuple):
                mref, mtext = masters[cell["formula"][1]]
                (mc, mr), (cc, cr) = split_ref(mref), split_ref(ref)
                cell["formula"] = shift_formula(mtext, cc - mc, cr - mr)
                cell["shared_from"] = mref

        validations = []

        def rule(dv, sqref, f1, f2):
            return {"cells": expand_sqref(sqref or ""), "type": dv.get("type"),
                    "operator": dv.get("operator", "between" if dv.get("type") in ("whole", "decimal") else None),
                    "formula1": f1.text if f1 is not None else None,
                    "formula2": f2.text if f2 is not None else None,
                    "allow_blank": dv.get("allowBlank") == "1",
                    "prompt": dv.get("prompt"), "prompt_title": dv.get("promptTitle"),
                    "error": dv.get("error"), "error_title": dv.get("errorTitle")}
        dvs = root.find(f"{{{M}}}dataValidations")
        for dv in (dvs if dvs is not None else []):
            validations.append(rule(dv, dv.get("sqref"), dv.find(f"{{{M}}}formula1"), dv.find(f"{{{M}}}formula2")))
        for dv in root.iter(f"{{{X14}}}dataValidation"):
            sq = dv.find(f"{{{XM}}}sqref")
            validations.append(rule(dv, sq.text if sq is not None else "",
                                    dv.find(f"{{{X14}}}formula1/{{{XM}}}f"), dv.find(f"{{{X14}}}formula2/{{{XM}}}f")))

        comments = {}
        rel_part = part.replace("worksheets/", "worksheets/_rels/") + ".rels"
        if rel_part in names:
            for rel in ET.fromstring(z.read(rel_part)).iter(f"{{{PKG_REL}}}Relationship"):
                if rel.get("Type", "").endswith("/comments"):
                    cpart = "xl/" + rel.get("Target").replace("../", "")
                    for cm in ET.fromstring(z.read(cpart)).iter(f"{{{M}}}comment"):
                        comments[cm.get("ref")] = re.sub(r"\s+", " ", text_of(cm)).strip()

        merged = [m.get("ref") for m in root.iter(f"{{{M}}}mergeCell")]
        cells_by_sheet[name] = cells
        book["sheets"][name] = {"state": state, "cells": cells, "validations": validations,
                                "comments": comments, "merged": merged}

    # resolve dropdown option lists and classify inputs
    for name, sheet in book["sheets"].items():
        for dv in sheet["validations"]:
            f1 = dv["formula1"] or ""
            if dv["type"] == "list" and f1.startswith('"'):
                dv["options"] = [o for o in f1.strip('"').split(",")]
            elif dv["type"] == "list" and f1:
                target = defined.get(f1, f1)
                opts = []
                for sname, ref in refs_in(target, name):
                    val = cells_by_sheet.get(sname, {}).get(ref, {}).get("value")
                    if val not in (None, ""):
                        opts.append(val)
                dv["options"] = opts
    readers = {}
    for name, sheet in book["sheets"].items():
        for ref, cell in sheet["cells"].items():
            if cell["formula"]:
                for sname, dep in refs_in(cell["formula"], name):
                    readers.setdefault((sname, dep), []).append(f"{name}!{ref}")

    for name, sheet in book["sheets"].items():
        inputs = {}
        dropdowns = {c: dv for dv in sheet["validations"] for c in dv["cells"]}
        for (sname, dep), who in readers.items():
            if sname != name:
                continue
            cell = sheet["cells"].get(dep)
            if cell is None or not cell["formula"]:
                inputs.setdefault(dep, {"read_by": []})["read_by"] += who
        for ref in dropdowns:
            inputs.setdefault(ref, {"read_by": []})
        for ref, info in inputs.items():
            cell = sheet["cells"].get(ref, {})
            info["value"] = cell.get("value")
            # a formula cell with a dropdown: the formula is the default and
            # picking a value replaces it
            info["override"] = bool(cell.get("formula"))
            if ref in dropdowns:
                info["options"] = dropdowns[ref].get("options")
                info["validation"] = {k: v for k, v in dropdowns[ref].items() if k != "cells" and v not in (None, False)}
            info["label"] = label_for(sheet["cells"], ref)
        sheet["inputs"] = dict(sorted(inputs.items(), key=lambda kv: split_ref(kv[0])[::-1]))
        sheet["formulas"] = {ref: {"formula": c["formula"], "cached": c["value"], "label": label_for(sheet["cells"], ref)}
                             for ref, c in sorted(sheet["cells"].items(), key=lambda kv: split_ref(kv[0])[::-1])
                             if c["formula"]}
        sheet["text"] = {ref: c["value"] for ref, c in sheet["cells"].items()
                         if not c["formula"] and isinstance(c["value"], str) and len(c["value"]) > 1}

    for sheet in book["sheets"].values():
        del sheet["cells"]

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(book, fh, ensure_ascii=False, indent=1)
    for name, sheet in book["sheets"].items():
        print(f"{book['workbook']} / {name}: {len(sheet['inputs'])} inputs "
              f"({sum(1 for i in sheet['inputs'].values() if i.get('options'))} dropdowns), "
              f"{len(sheet['formulas'])} formulas, {len(sheet['comments'])} comments")


def label_for(cells, ref):
    """Nearest text to the left in the row, and nearest text above in the column."""
    c, r = split_ref(ref)
    left = up = None
    for cc in range(c - 1, 0, -1):
        v = cells.get(f"{col_name(cc)}{r}", {}).get("value")
        if isinstance(v, str) and v.strip() and not cells[f"{col_name(cc)}{r}"]["formula"]:
            left = v.strip()[:60]
            break
    for rr in range(r - 1, 0, -1):
        v = cells.get(f"{col_name(c)}{rr}", {}).get("value")
        if isinstance(v, str) and v.strip() and not cells[f"{col_name(c)}{rr}"]["formula"]:
            up = v.strip()[:60]
            break
    return {"row": left, "col": up}


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
