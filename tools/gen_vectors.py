#!/usr/bin/env python3
"""Generate golden test vectors by recalculating the vendor workbooks in LibreOffice.

    gen_vectors.py <models.tsv> <inventory_dir> <reference_dir> <out_dir> [count] [model...]

For each model: load the workbook headless, recalculate once with the saved
inputs and compare every formula cell with the value the authoring app cached
(the harness self-check), then write <count> vectors of randomised inputs with
every formula cell's recalculated result to <out_dir>/<model>.json. The vectors
are implementation-neutral, so a later C/Rust port can be checked against them.

Needs the system python3 with the LibreOffice UNO bridge (python3-uno).
"""
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

import uno
from com.sun.star.beans import PropertyValue

WIDTHS = [320, 352, 640, 704, 720, 800, 960, 1024, 1280, 1920, 2048, 2304, 2560, 2592, 2688, 2880, 3840]
HEIGHTS = [180, 240, 288, 360, 480, 576, 600, 720, 960, 1080, 1296, 1440, 1520, 1536, 1620, 1944, 2160]


def prop(name, value):
    p = PropertyValue()
    p.Name, p.Value = name, value
    return p


def connect(pipe, profile):
    # a private profile: a desktop LibreOffice holding the user profile lock
    # makes loadComponentFromURL return None with no error
    proc = subprocess.Popen(["soffice", "--headless", "--invisible", "--norestore", "--nologo",
                             "--nodefault", f"-env:UserInstallation={uno.systemPathToFileUrl(profile)}",
                             f"--accept=pipe,name={pipe};urp;StarOffice.ComponentContext"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
    for _ in range(60):
        try:
            ctx = resolver.resolve(f"uno:pipe,name={pipe};urp;StarOffice.ComponentContext")
            desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
            return proc, desktop
        except Exception:
            time.sleep(0.5)
    proc.kill()
    raise SystemExit("cannot reach soffice")


def repack(src, tmpdir):
    """Copy of the workbook without zip directory entries, which LibreOffice
    refuses to open (loadComponentFromURL returns None)."""
    dst = os.path.join(tmpdir, os.path.basename(src))
    with zipfile.ZipFile(src) as zi, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zo:
        names = [n for n in zi.namelist() if not n.endswith("/")]
        for n in sorted(names, key=lambda n: (n != "[Content_Types].xml", n)):
            zo.writestr(n, zi.read(n))
    return dst


def read_cell(cell):
    """Value as the vectors store it: number, string, or {'error': code}."""
    err = cell.getError()
    if err:
        return {"error": err}
    t = cell.getType().value
    if t == "VALUE":
        v = cell.getValue()
        return int(v) if float(v).is_integer() else v
    if t == "TEXT":
        return cell.getString()
    if t == "FORMULA":
        if cell.FormulaResultType2 == uno.getConstantByName("com.sun.star.sheet.FormulaResult.VALUE"):
            v = cell.getValue()
            return int(v) if float(v).is_integer() else v
        return cell.getString()
    return None


def write_cell(cell, value):
    if isinstance(value, bool):
        cell.setFormula("=TRUE()" if value else "=FALSE()")
    elif isinstance(value, (int, float)):
        cell.setValue(value)
    elif value is None:
        cell.setString("")
    else:
        cell.setString(str(value))


def same(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))
    if isinstance(a, bool) or isinstance(b, bool):
        return str(a).upper() == str(b).upper()
    return a == b or (a in (None, "") and b in (None, ""))


def domain(ref, info):
    """Candidate values for one input cell."""
    opts = info.get("options")
    default = info.get("value")
    if opts:
        return [o for o in opts if o not in (None, "")] or [default]
    label = " ".join(str(x) for x in (info["label"].get("row"), info["label"].get("col")) if x).lower()
    if isinstance(default, bool):
        return [True, False]
    if isinstance(default, (int, float)):
        d = default
        if d in WIDTHS or "宽" in label or "width" in label:
            return WIDTHS
        if d in HEIGHTS or "高" in label or "height" in label:
            return HEIGHTS
        if "nrvb" in label:
            return [1, 2, 3, 4]
        if d == 0:
            return [0, 0, 1, 2]
        return sorted({d, d * 2, max(1, d // 2) if isinstance(d, int) else d / 2})
    return [default]


def main():
    models_tsv, inv_dir, ref_dir, out_dir = sys.argv[1:5]
    count = int(sys.argv[5]) if len(sys.argv) > 5 else 200
    only = set(sys.argv[6:])
    os.makedirs(out_dir, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="rmemcalc-lo-")
    proc, desktop = connect(f"rmemcalc{os.getpid()}", profile)
    try:
        for row in open(models_tsv, encoding="utf-8"):
            if not row.strip() or row.startswith("#"):
                continue
            model, inv, sheet_name, socs = row.rstrip("\n").split("\t")
            if only and model not in only:
                continue
            book = json.load(open(os.path.join(inv_dir, inv), encoding="utf-8"))
            sh = book["sheets"][sheet_name]
            path = repack(os.path.join(ref_dir, book["workbook"]), profile)
            # a freshly created profile is still initialising for the first loads
            for _ in range(20):
                doc = desktop.loadComponentFromURL(uno.systemPathToFileUrl(path), "_blank", 0,
                                                   (prop("Hidden", True),))
                if doc is not None:
                    break
                time.sleep(1)
            if doc is None:
                raise SystemExit(f"LibreOffice could not open {path} (is libreoffice-calc installed?)")
            sheet = doc.Sheets.getByName(sheet_name)
            doc.calculateAll()
            mismatch = []
            for ref, f in sh["formulas"].items():
                got = read_cell(sheet.getCellRangeByName(ref))
                if not same(got, f["cached"]):
                    mismatch.append((ref, f["cached"], got))
            rng = random.Random(f"{model}:{book['workbook']}")
            vectors = []
            for _ in range(count):
                inputs = {ref: rng.choice(domain(ref, info)) for ref, info in sh["inputs"].items()}
                for ref, value in inputs.items():
                    write_cell(sheet.getCellRangeByName(ref), value)
                doc.calculateAll()
                outputs = {ref: read_cell(sheet.getCellRangeByName(ref)) for ref in sh["formulas"]}
                vectors.append({"inputs": inputs, "outputs": outputs})
            doc.close(True)
            result = {"model": model, "workbook": book["workbook"], "sheet": sheet_name,
                      "selfcheck_mismatches": mismatch, "vectors": vectors}
            with open(os.path.join(out_dir, f"{model}.json"), "w", encoding="utf-8") as fh:
                json.dump(result, fh, ensure_ascii=False)
            errs = sum(1 for v in vectors for o in v["outputs"].values() if isinstance(o, dict))
            print(f"{model}: self-check {len(sh['formulas']) - len(mismatch)}/{len(sh['formulas'])} cells match cached; "
                  f"{len(vectors)} vectors, {errs} error cells", flush=True)
            for ref, want, got in mismatch[:5]:
                print(f"    {ref}: cached {want!r} recalculated {got!r}", flush=True)
    finally:
        try:
            desktop.terminate()
        except Exception:
            pass
        proc.wait(timeout=30)
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    main()
