# ingenic-rmem-calc

Command-line replacement for Ingenic's per-SoC rmem calculator spreadsheets.

Bring-up stage: a Python 3 (stdlib only) reference implementation, validated
cell-by-cell against the vendor workbooks recalculated in LibreOffice. The
golden vectors are plain JSON so a later C/Rust port can be held to the same
results.

## Use

    ./rmem-calc models                 # the 17 calculators and their sheets
    ./rmem-calc socs                   # SoC models: RAM, regions, calculators
    ./rmem-calc inputs t31 [--grep 主路] # inputs: label, default, options/limits
    ./rmem-calc notes t33_single       # the sheet's own instructions
    ./rmem-calc calc t31 D8=2560 G8=1440 J16=HEVC --soc t31x
    ./rmem-calc calc t41_dual --soc t41nq --mode dual --json --all
    ./rmem-calc layout --soc t40xp --rmem 30

`calc` takes inputs as `CELL=VALUE`, the cell names the sheet uses, and
applies the sheet's own validation (dropdown lists, number ranges; `--force`
skips it). Cells marked `(computed)` in `inputs` are formulas the sheet lets
you override by picking a value. Output is every cmdline the sheet builds;
`--all` adds every formula cell.

With `--soc`, the SoC's DRAM size and part number drive the sheet's own
selectors, and the sheet's rmem is laid out in that SoC's real memory map:

- T10/T20: `mem | ispmem | rmem` (ispmem from the sheet unless `--ispmem`)
- T40/T41: `mem | rmem | nmem`; the sheets do not reserve the NNA's nmem, so
  it is added here (vendor U-Boot default: none on 64 MB parts, 16M at
  128 MB, 28M from 256 MB; `--nmem` overrides)
- everything else: `mem | rmem`

and printed as U-Boot bootargs (MB and KB), thingino uenv lines and the
thingino defconfig values. A1 has no vendor sheet; `layout` places a given
rmem for it (or any SoC).

## Layout

- `reference/` - the vendor workbooks being reproduced; `sources.tsv` records
  where each came from (SDK build, original file name) and its CRC is in the name.
- `models.tsv` - the calculators to port: model, inventory file, sheet, SoCs.
- `inventory/` - per-workbook JSON of every input (with dropdown options),
  formula cell and note, from `tools/sheet_inventory.py`; `CHECKLIST.md` is the
  readable feature list from `tools/inventory_report.py`.
- `vectors/` - golden test vectors from `tools/gen_vectors.py`.

## Regenerating

    python3 tools/sheet_inventory.py reference/<book>.xlsx inventory/<book>.json
    python3 tools/inventory_report.py models.tsv inventory inventory/CHECKLIST.md
    /usr/bin/python3 tools/gen_vectors.py models.tsv inventory reference vectors [count] [model...]

`gen_vectors.py` needs LibreOffice Calc and its UNO bridge (`libreoffice-calc`,
`python3-uno`); it first checks that LibreOffice reproduces the values the
authoring application cached in each workbook.
