"""The models must reproduce the vendor sheets exactly.

    python3 -m unittest discover -s tests

- every formula cell of every LibreOffice vector (vectors/*.json)
- every cmdline string the sheets emit, rebuilt by socs.layout (both units)
- every inventoried input and formula cell has a counterpart in its model
"""
import importlib
import json
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from ingenic_rmem import socs, xl  # noqa: E402
from ingenic_rmem.cli import MODELS  # noqa: E402

# region placement family per model (ispmem on T10/T20 sheets, nmem never in a sheet)
MODEL_FAMILY = {"t20_t10": "t20"}


def models_tsv():
    rows = {}
    with open(os.path.join(ROOT, "models.tsv"), encoding="utf-8") as fh:
        for row in fh:
            if row.strip() and not row.startswith("#"):
                model, inv, sheet, socs_ = row.rstrip("\n").split("\t")
                rows[model] = (inv, sheet)
    return rows


def vectors(model):
    with open(os.path.join(ROOT, "vectors", f"{model}.json"), encoding="utf-8") as fh:
        return json.load(fh)["vectors"]


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


class Parity(unittest.TestCase):
    def test_every_cell_of_every_vector(self):
        for model in MODELS:
            mod = importlib.import_module(f"ingenic_rmem.models.{model}")
            with self.subTest(model=model):
                bad = []
                vecs = vectors(model)
                self.assertGreater(len(vecs), 0)
                for i, vec in enumerate(vecs):
                    out = mod.compute(vec["inputs"])
                    for ref, want in vec["outputs"].items():
                        if not same(want, out.get(ref)):
                            bad.append((i, ref, want, out.get(ref)))
                self.assertEqual(bad[:5], [], f"{len(bad)} mismatching cells")


class Layout(unittest.TestCase):
    def test_sheet_cmdlines_rebuilt(self):
        for model in MODELS:
            mod = importlib.import_module(f"ingenic_rmem.models.{model}")
            fam = MODEL_FAMILY.get(model, "t31")
            with self.subTest(model=model):
                checked = 0
                for vec in vectors(model):
                    out = mod.compute(vec["inputs"])
                    texts = [v for v in out.values() if isinstance(v, str) and "mem=" in v and "rmem=" in v]
                    for text in texts:
                        regions = socs.parse_cmdline(text)
                        named = {n: kb for n, kb, _ in regions}
                        total = sum(named.values())
                        if named.get("mem", 0) <= 0 or total % 1024:
                            continue
                        unit = "K" if "K@" in text else "M"
                        got = socs.layout(fam, total // 1024, named["rmem"], 0, named.get("ispmem", 0), unit)
                        self.assertEqual(got, regions, f"{model}: {text}")
                        checked += 1
                self.assertGreater(checked, 0, f"{model}: no cmdline checked")


class Coverage(unittest.TestCase):
    def test_every_inventoried_cell_is_modelled(self):
        for model, (inv, sheet) in models_tsv().items():
            mod = importlib.import_module(f"ingenic_rmem.models.{model}")
            with open(os.path.join(ROOT, "inventory", inv), encoding="utf-8") as fh:
                sh = json.load(fh)["sheets"][sheet]
            with self.subTest(model=model):
                self.assertEqual(set(sh["inputs"]) - set(mod.INPUTS), set(), "inputs missing")
                self.assertEqual(set(sh["formulas"]) - set(mod.compute()), set(), "formula cells missing")
                for ref, info in sh["inputs"].items():
                    self.assertEqual(mod.INPUTS[ref]["options"], info.get("options"), f"{ref} options")

    def test_cli_knows_every_model(self):
        self.assertEqual(set(MODELS), set(models_tsv()))


if __name__ == "__main__":
    unittest.main()
