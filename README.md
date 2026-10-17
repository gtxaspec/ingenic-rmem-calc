# ingenic-rmem-calc

Command-line replacement for Ingenic's per-SoC rmem calculator spreadsheets.

Bring-up stage: a Python 3 (stdlib only) reference implementation, validated
cell-by-cell against the vendor workbooks recalculated in LibreOffice. The
golden vectors are plain JSON so a later C/Rust port can be held to the same
results.

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
