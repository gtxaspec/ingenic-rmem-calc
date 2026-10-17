"""SoC models, their RAM, which vendor calculators apply, and memory layout.

RAM sizes come from thingino-firmware soc/ingenic/*.mk. Region order and the
NMEM defaults come from the vendor U-Boot BOOTARGS_COMMON (isvp_t40.h,
isvp_t41.h): T40/T41 put the NNA's nmem above rmem, T10/T20 put ispmem
between mem and rmem, every other family has mem and rmem only.
"""
import re

# calculator models per family: (model, what it covers)
FAMILY_MODELS = {
    "t10": [("t20_t10", "single camera")],
    "t20": [("t20_t10", "single camera")],
    "t21": [("t21", "single camera")],
    "t23": [("t23_single", "single camera"), ("t23_dual", "dual camera"), ("t23_triple", "triple camera")],
    "t30": [("t30", "single camera")],
    "t31": [("t31", "single camera")],
    "c100": [("t31", "single camera (the C100 sheet is the T31 one)")],
    "t32": [("t32_single", "single camera"), ("t32_multi", "multi camera")],
    "t32pro": [("t32pro_single", "single camera"), ("t32pro_multi", "multi camera")],
    "t33": [("t33_single", "single camera"), ("t33_multi", "multi camera")],
    "t40": [("t40", "single and dual camera (V1.2)"), ("t40_v11", "single and dual camera (V1.1)")],
    "t41": [("t41", "single camera"), ("t41_dual", "dual camera (the T40 V1.1 sheet, as Ingenic ships it for T41)")],
    "a1": [],
}

# region order above mem=, per family
FAMILY_REGIONS = {"t10": ("ispmem", "rmem"), "t20": ("ispmem", "rmem"),
                  "t40": ("rmem", "nmem"), "t41": ("rmem", "nmem")}

SOC_MODELS = {
    "a1a": ("a1", 512), "a1l": ("a1", 128), "a1n": ("a1", 256), "a1nt": ("a1", 256), "a1x": ("a1", 256),
    "c100": ("c100", 128),
    "t10a": ("t10", 64), "t10l": ("t10", 64), "t10n": ("t10", 64),
    "t20l": ("t20", 64), "t20n": ("t20", 64), "t20x": ("t20", 128), "t20z": ("t20", 64),
    "t21l": ("t21", 64), "t21n": ("t21", 64), "t21x": ("t21", 128), "t21zl": ("t21", 64), "t21zn": ("t21", 64),
    "t23dl": ("t23", 32), "t23n": ("t23", 64), "t23zn": ("t23", 64),
    "t30a": ("t30", 128), "t30l": ("t30", 64), "t30n": ("t30", 64), "t30x": ("t30", 128),
    "t31a": ("t31", 128), "t31al": ("t31", 128), "t31l": ("t31", 64), "t31lc": ("t31", 64),
    "t31n": ("t31", 64), "t31x": ("t31", 128), "t31zl": ("t31", 64), "t31zx": ("t31", 128),
    "t32lq": ("t32", 64), "t32nq": ("t32", 128), "t32vn": ("t32", 128),
    "t33dl": ("t33", 64), "t33l": ("t33", 64), "t33lq": ("t33", 64), "t33n": ("t33", 64),
    "t33vl": ("t33", 64), "t33vn": ("t33", 64), "t33zl": ("t33", 64), "t33zn": ("t33", 64),
    "t40a": ("t40", 128), "t40n": ("t40", 128), "t40nn": ("t40", 128), "t40xp": ("t40", 256),
    "t41a": ("t41", 512), "t41lq": ("t41", 64), "t41nq": ("t41", 128), "t41zl": ("t41", 64),
    "t41zn": ("t41", 128), "t41zx": ("t41", 256),
}


def family_of(name):
    """Family for a model name (t41nq) or a bare family (t41, t32pro)."""
    name = name.lower()
    if name in SOC_MODELS:
        return SOC_MODELS[name][0]
    if name in FAMILY_MODELS:
        return name
    raise KeyError(f"unknown SoC {name!r}")


def default_nmem_mb(family, ram_mb):
    """Vendor U-Boot NMEM: none on 64 MB parts, 16M at 128 MB, 28M from 256 MB."""
    if family not in ("t40", "t41") or ram_mb <= 64:
        return 0
    return 16 if ram_mb <= 128 else 28


def default_ispmem_mb(family):
    return 8 if family in ("t10", "t20") else 0


def layout(family, ram_mb, rmem_kb, nmem_kb=0, ispmem_kb=0, unit="M"):
    """Place mem/ispmem/rmem/nmem the way the family's U-Boot does.

    unit "M" rounds every reservation up to whole MiB, as the sheets' MB
    cmdline does; unit "K" keeps KiB, as their KB cmdline does.
    Returns [(name, size_kb, offset_bytes)] in address order.
    """
    def size(kb):
        return -(-kb // 1024) * 1024 if unit == "M" else kb
    sizes = {"rmem": size(rmem_kb), "nmem": size(nmem_kb), "ispmem": size(ispmem_kb)}
    above = [r for r in FAMILY_REGIONS.get(family, ("rmem",)) if sizes[r] or r == "rmem"]
    total_kb = ram_mb * 1024
    mem_kb = total_kb - sum(sizes[r] for r in above)
    if mem_kb <= 0:
        raise ValueError(f"reservations {sum(sizes[r] for r in above)}K leave no RAM for Linux in {ram_mb}M")
    regions, offset = [("mem", mem_kb, 0)], mem_kb * 1024
    for r in above:
        regions.append((r, sizes[r], offset))
        offset += sizes[r] * 1024
    return regions


def bootargs(regions, unit="M"):
    """U-Boot cmdline fragment, e.g. 'mem=106M@0x0 rmem=22M@0x6A00000'."""
    parts = []
    for name, kb, off in regions:
        n = kb // 1024 if unit == "M" else kb
        parts.append(f"{name}={n}{unit}@0x{off:X}")
    return " ".join(parts)


CMDLINE = re.compile(r"(?P<name>[a-z]*mem)=(?P<n>\d+)(?P<unit>[KM])@0x(?P<off>[0-9A-Fa-f]+)")


def parse_cmdline(text):
    """[(name, size_kb, offset_bytes)] from a 'mem=..M@0x0 rmem=..' string."""
    out = []
    for m in CMDLINE.finditer(text):
        kb = int(m.group("n")) * (1024 if m.group("unit") == "M" else 1)
        out.append((m.group("name"), kb, int(m.group("off"), 16)))
    return out
