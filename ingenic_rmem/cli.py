"""rmem-calc: the Ingenic rmem calculator spreadsheets as a command line tool."""
import argparse
import importlib
import json
import re
import sys

from . import socs, xl

MODELS = ["t20_t10", "t30", "t21", "t31", "t23_single", "t23_dual", "t23_triple", "t40", "t40_v11", "t41", "t41_dual",
          "t32_single", "t32_multi", "t32pro_single", "t32pro_multi", "t33_single", "t33_multi"]


def load(model):
    if model not in MODELS:
        raise SystemExit(f"unknown model {model!r}; see 'rmem-calc models'")
    return importlib.import_module(f".models.{model}", __package__)


def parse_value(text):
    if text.upper() in ("TRUE", "FALSE"):
        return text.upper() == "TRUE"
    try:
        f = float(text)
        return int(f) if f.is_integer() else f
    except ValueError:
        return text


def check(ref, info, value):
    """The sheet's own data validation, or None when the value is acceptable."""
    opts = info.get("options")
    val = info.get("validation") or {}
    if not opts and val.get("type") == "custom" and (val.get("formula1") or "").startswith('"'):
        # the T40 V1.1 / T41 dual sheets declare their Yes/No list as type custom
        opts = val["formula1"].strip('"').split(",")
    if opts:
        if not any(str(o).lower() == str(value).lower() for o in opts):
            return f"{ref}: {value!r} is not one of {opts}"
        return None
    val = info.get("validation") or {}
    if val.get("type") in ("whole", "decimal"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"{ref}: {value!r} must be a number"
        if val["type"] == "whole" and value != int(value):
            return f"{ref}: {value!r} must be a whole number"
        lo, hi = (float(val[k]) if val.get(k) not in (None, "") else None for k in ("formula1", "formula2"))
        op = val.get("operator", "between")
        bad = ((op == "between" and ((lo is not None and value < lo) or (hi is not None and value > hi)))
               or (op == "greaterThanOrEqual" and value < lo) or (op == "greaterThan" and value <= lo)
               or (op == "lessThanOrEqual" and value > lo) or (op == "lessThan" and value >= lo))
        if bad:
            rng = f"{val.get('formula1')}..{val.get('formula2')}" if op == "between" else f"{op} {lo}"
            return f"{ref}: {value!r} outside {rng}" + (f" ({val['error']})" if val.get("error") else "")
    return None


def soc_inputs(mod, soc, ram):
    """Sheet selector cells for this SoC: the DRAM size dropdown ('64M'/'128M')
    and the part number dropdown (T10/T20, T41L/T41N), first of each.
    Returns ([(cell, value)], [notes])."""
    sets, notes, ram_done, part_done = [], [], False, False
    soc_u = soc.upper()
    for ref, info in mod.INPUTS.items():
        opts = [str(o) for o in (info.get("options") or [])]
        if not opts or info.get("override"):
            continue
        if not ram_done and all(re.fullmatch(r"\d+M", o.upper()) for o in opts):
            sizes = sorted(int(o[:-1]) for o in opts)
            pick = ram if ram in sizes else min(sizes, key=lambda s: (abs(s - ram), -s))
            if pick != ram:
                notes.append(f"the sheet offers {'/'.join(f'{s}M' for s in sizes)} DRAM; it calculates for {pick}M")
            sets.append((ref, f"{pick}M"))
            ram_done = True
        elif not part_done and all(re.fullmatch(r"(T\d\d[A-Z]*|C100|A1[A-Z]*)", o.upper()) for o in opts):
            up = [o.upper() for o in opts]
            if soc_u in up:
                choice = opts[up.index(soc_u)]
            else:
                pref = [o for o in opts if soc_u.startswith(o.upper())]
                if pref:
                    choice = max(pref, key=len)
                elif any(o.upper().endswith("L") for o in opts):
                    # T41L marks the 64 MB parts, T41N the others
                    lo = [o for o in opts if o.upper().endswith("L")]
                    hi = [o for o in opts if not o.upper().endswith("L")]
                    choice = (lo if ram <= 64 else hi or lo)[0]
                else:
                    choice = opts[0]
                if choice.upper() != soc_u:
                    notes.append(f"part number {choice} used for {soc_u}")
            sets.append((ref, choice))
            part_done = True
    return sets, notes


def cmdlines(mod, out):
    """Every cmdline the sheet produces: [(cell, label, text)]."""
    res = []
    for ref, val in out.items():
        if isinstance(val, str) and "mem=" in val and "rmem=" in val:
            res.append((ref, mod.LABELS.get(ref, ""), val))
    return res


def show(v):
    if isinstance(v, xl.XLError):
        return f"#{v.kind}"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def cmd_models(args):
    for m in MODELS:
        mod = load(m)
        print(f"{m:15s} {mod.SOCS:32s} {len(mod.INPUTS):4d} inputs {len(mod.LABELS):4d} formulas"
              f"  [{mod.SOURCE['workbook']} / {mod.SOURCE['sheet'].strip()}]")


def cmd_socs(args):
    for name, (fam, ram) in sorted(socs.SOC_MODELS.items()):
        regions = "mem " + " ".join(socs.FAMILY_REGIONS.get(fam, ("rmem",)))
        calcs = ", ".join(m for m, _ in socs.FAMILY_MODELS.get(fam, [])) or "none (no vendor sheet)"
        print(f"{name:6s} {fam:5s} {ram:4d}M  {regions:20s} {calcs}")


def cmd_inputs(args):
    mod = load(args.model)
    for ref, info in mod.INPUTS.items():
        if args.grep and args.grep.lower() not in (ref + " " + info["label"]).lower():
            continue
        what = ""
        if info.get("options"):
            what = "one of " + ", ".join(str(o) for o in info["options"])
        elif info.get("validation"):
            v = info["validation"]
            what = f"{v.get('type')} {v.get('operator', '')} {v.get('formula1', '')}..{v.get('formula2', '')}".strip()
        default = "(computed)" if info.get("override") else repr(info["default"])
        print(f"{ref:6s} {info['label'][:48]:48s} default {default:14s} {what}")


def cmd_notes(args):
    mod = load(args.model)
    for ref, text in mod.NOTES:
        print(f"[{ref}] {text}\n")


def cmd_calc(args):
    mod = load(args.model)
    values = {}
    for a in args.set:
        if "=" not in a:
            raise SystemExit(f"expected CELL=VALUE, got {a!r}")
        ref, raw = a.split("=", 1)
        ref = ref.strip().upper()
        if ref not in mod.INPUTS:
            raise SystemExit(f"{ref} is not an input of {args.model}; see 'rmem-calc inputs {args.model}'")
        values[ref] = parse_value(raw.strip())

    fam = ram = None
    notes = []
    if args.soc:
        fam = socs.family_of(args.soc)
        ram = args.ram or socs.SOC_MODELS.get(args.soc.lower(), (fam, None))[1]
        if not ram:
            raise SystemExit(f"{args.soc} is a family name; give --ram")
        sets, notes = soc_inputs(mod, args.soc, ram)
        for ref, val in sets:
            values.setdefault(ref, val)

    problems = [p for ref, v in values.items() if (p := check(ref, mod.INPUTS[ref], v))]
    if problems and not args.force:
        raise SystemExit("rejected by the sheet's validation (use --force to calculate anyway):\n  " + "\n  ".join(problems))

    out = mod.compute(values)
    lines = cmdlines(mod, out)
    result = {"model": args.model, "source": mod.SOURCE, "inputs": values, "cmdlines": [], "layout": None}
    for ref, label, text in lines:
        result["cmdlines"].append({"cell": ref, "label": label, "value": text})

    if fam:
        try:
            result["layout"] = soc_layout(args, fam, ram, lines)
        except ValueError as e:
            raise SystemExit(f"{args.soc}: {e}")
        result["notes"] = notes + result["layout"].pop("notes")

    if args.json:
        if args.all:
            result["cells"] = {r: show(v) for r, v in out.items()}
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return
    for c in result["cmdlines"]:
        print(f"{c['cell']:5s} {c['value']:62s} {c['label']}")
    if result["layout"]:
        lay = result["layout"]
        print(f"\n{args.soc} ({fam}, {ram}M): rmem from {lay['from_cell']} ({lay['from_label'].rstrip(':')})"
              + (f", nmem {lay['nmem_mb']}M" if lay["nmem_mb"] else "")
              + (f", ispmem {lay['ispmem_kb']}K" if lay["ispmem_kb"] else ""))
        for n in result["notes"]:
            print(f"  note: {n}")
        print(f"  bootargs   {lay['bootargs_M']}")
        print(f"  bootargs   {lay['bootargs_K']}")
        print("  uenv       " + "  ".join(lay["uenv"]))
        print("  defconfig  " + "  ".join(lay["defconfig"]))
    if args.all:
        print()
        for r, v in out.items():
            print(f"{r:6s} {show(v)[:60]:60s} {mod.LABELS.get(r, '')}")


def soc_layout(args, fam, ram, lines):
    """The sheet's rmem placed in this SoC's real memory map (adds nmem/ispmem)."""
    # the Chinese sheets say 单摄/双摄, the English ones Single/Dual camera
    words = {"single": ("单摄", "single"), "dual": ("双摄", "dual")}.get((args.mode or "").lower(),
                                                                    (args.mode,) if args.mode else ())
    pick = None
    for ref, label, text in lines:
        if "K@" not in text or (words and not any(w.lower() in label.lower() for w in words)):
            continue
        if f"DDR{ram}M" in label:
            pick = (ref, text)
            break
        if pick is None and "DDR" not in label:
            pick = (ref, text)
    if pick is None:
        raise SystemExit(f"the sheet has no KB cmdline for {ram}M" + (f" in mode {args.mode}" if mode else ""))
    regions = {name: kb for name, kb, _ in socs.parse_cmdline(pick[1])}
    rmem_kb = regions["rmem"]
    if fam not in ("t10", "t20"):
        ispmem_kb = 0
    elif args.ispmem is not None:
        ispmem_kb = args.ispmem * 1024
    else:
        ispmem_kb = regions.get("ispmem", 0)
    nmem_mb = args.nmem if args.nmem is not None else socs.default_nmem_mb(fam, ram)
    m = socs.layout(fam, ram, rmem_kb, nmem_mb * 1024, ispmem_kb, "M")
    k = socs.layout(fam, ram, rmem_kb, nmem_mb * 1024, ispmem_kb, "K")
    # without the extra regions, this layout must be the sheet's own cmdline
    notes = []
    sheet_regions = socs.parse_cmdline(pick[1])
    sheet_total = sum(kb for _, kb, _ in sheet_regions)
    if sheet_total != ram * 1024:
        notes.append(f"the sheet's cmdline covers {sheet_total // 1024}M; laid out for {ram}M")
        agrees = None
    else:
        plain = socs.layout(fam, ram, rmem_kb, 0, regions.get("ispmem", 0) if fam in ("t10", "t20") else 0, "K")
        agrees = plain == sheet_regions
        if not agrees:
            notes.append("WARNING: the plain layout differs from the sheet's own cmdline")
    named = {name: (kb, off) for name, kb, off in m}
    uenv = [f"osmem={named['mem'][0] // 1024}M@0x0"]
    for name in ("ispmem", "rmem", "nmem"):
        if name in named:
            uenv.append(f"{name}={named[name][0] // 1024}M@0x{named[name][1]:X}")
    defconfig = [f"BR2_THINGINO_RMEM_MB={named['rmem'][0] // 1024}"]
    if fam in ("t40", "t41"):
        defconfig.append(f"BR2_THINGINO_NMEM_MB={named.get('nmem', (0, 0))[0] // 1024}")
    if fam in ("t10", "t20"):
        defconfig.append(f"BR2_THINGINO_ISPMEM_MB={named.get('ispmem', (0, 0))[0] // 1024}")
    return {"from_cell": pick[0], "from_label": dict((r, l) for r, l, _ in lines)[pick[0]], "rmem_kb": rmem_kb, "nmem_mb": nmem_mb, "ispmem_kb": ispmem_kb,
            "bootargs_M": socs.bootargs(m, "M"), "bootargs_K": socs.bootargs(k, "K"),
            "uenv": uenv, "defconfig": defconfig, "matches_sheet_cmdline": agrees, "notes": notes}


def cmd_layout(args):
    fam = socs.family_of(args.soc)
    ram = args.ram or socs.SOC_MODELS.get(args.soc.lower(), (fam, None))[1]
    if not ram:
        raise SystemExit("give --ram for a bare family name")
    nmem = args.nmem if args.nmem is not None else socs.default_nmem_mb(fam, ram)
    ispmem = args.ispmem if args.ispmem is not None else socs.default_ispmem_mb(fam)
    regions = socs.layout(fam, ram, int(args.rmem * 1024), nmem * 1024, ispmem * 1024, "M")
    print(socs.bootargs(regions, "M"))


def main(argv=None):
    p = argparse.ArgumentParser(prog="rmem-calc", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("models", help="list the calculator models").set_defaults(fn=cmd_models)
    sub.add_parser("socs", help="list SoC models, RAM, regions and calculators").set_defaults(fn=cmd_socs)
    s = sub.add_parser("inputs", help="list a model's inputs, defaults and options")
    s.add_argument("model")
    s.add_argument("--grep")
    s.set_defaults(fn=cmd_inputs)
    s = sub.add_parser("notes", help="print the sheet's instructions and notes")
    s.add_argument("model")
    s.set_defaults(fn=cmd_notes)
    s = sub.add_parser("calc", help="calculate, like editing the sheet")
    s.add_argument("model")
    s.add_argument("set", nargs="*", metavar="CELL=VALUE")
    s.add_argument("--soc", help="SoC model (t31x, t41nq...) to lay the result out for")
    s.add_argument("--ram", type=int, help="DRAM MB, if not the SoC model's")
    s.add_argument("--nmem", type=int, help="NNA reservation MB (T40/T41; vendor default by RAM)")
    s.add_argument("--ispmem", type=int, help="ispmem MB (T10/T20; default: the sheet's)")
    s.add_argument("--mode", help="pick the cmdline whose label contains this (e.g. 双摄 for dual camera)")
    s.add_argument("--all", action="store_true", help="also print every formula cell")
    s.add_argument("--json", action="store_true")
    s.add_argument("--force", action="store_true", help="ignore the sheet's input validation")
    s.set_defaults(fn=cmd_calc)
    s = sub.add_parser("layout", help="memory map for a given rmem without a sheet (A1, manual values)")
    s.add_argument("--soc", required=True)
    s.add_argument("--rmem", type=float, required=True, help="MB")
    s.add_argument("--ram", type=int)
    s.add_argument("--nmem", type=int)
    s.add_argument("--ispmem", type=int)
    s.set_defaults(fn=cmd_layout)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
