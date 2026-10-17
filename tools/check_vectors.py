#!/usr/bin/env python3
"""Check generated models against the LibreOffice golden vectors.

    check_vectors.py <vectors_dir> [model...]

Compares every formula cell of every vector; prints per-model totals and the
first mismatches. Exits non-zero on any mismatch or missing formula cell.
"""
import importlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from ingenic_rmem import xl  # noqa: E402


def same(want, got):
    if isinstance(want, dict) and "error" in want:
        return isinstance(got, xl.XLError)
    if isinstance(got, xl.XLError):
        return False
    if isinstance(want, bool) or isinstance(got, bool):
        return str(want).upper() == str(got).upper()
    if isinstance(want, (int, float)) and isinstance(got, (int, float)):
        return abs(want - got) <= 1e-9 * max(1.0, abs(want), abs(got))
    if want in (None, "") and got in (None, ""):
        return True
    return want == got


def main():
    vec_dir = sys.argv[1]
    only = set(sys.argv[2:])
    bad = 0
    for name in sorted(os.listdir(vec_dir)):
        if not name.endswith(".json"):
            continue
        model = name[:-5]
        if only and model not in only:
            continue
        data = json.load(open(os.path.join(vec_dir, name), encoding="utf-8"))
        mod = importlib.import_module(f"ingenic_rmem.models.{model}")
        cells = fails = 0
        shown = 0
        for i, vec in enumerate(data["vectors"]):
            out = mod.compute(vec["inputs"])
            for ref, want in vec["outputs"].items():
                cells += 1
                if ref not in out:
                    fails += 1
                    if shown < 4:
                        print(f"  {model} vector {i} {ref}: cell missing from the model")
                        shown += 1
                    continue
                if not same(want, out[ref]):
                    fails += 1
                    if shown < 4:
                        print(f"  {model} vector {i} {ref}: sheet {want!r} model {out[ref]!r}")
                        shown += 1
        bad += fails
        print(f"{model}: {cells - fails}/{cells} cells match over {len(data['vectors'])} vectors"
              + ("" if not fails else "  <-- MISMATCH"))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
