# Rebuilding the PDFs

Both documents are generated from code, so they cannot drift from the
repository:

```bash
pip install uharfbuzz fonttools
python tools/build_guide_pdf.py     # docs/PROJECT_GUIDE_BN.pdf   (Bengali)
python tools/build_code_pdf.py      # docs/CODE_WALKTHROUGH.pdf  (English)
```

## Why not WeasyPrint / Chromium / LaTeX?

Bengali needs proper shaping (conjuncts, reordered vowel signs) *and* text that
stays selectable. That normally means pango/cairo or a headless browser — heavy
system dependencies that are not always available. `tools/pdfkit.py` is a ~600
line writer that:

1. shapes with **HarfBuzz** (`uharfbuzz` wheel) — real glyph ids, real advances,
   real mark positioning;
2. embeds only the glyphs used (**fontTools** subsetting, `retain_gids`);
3. writes a **ToUnicode CMap**, so the PDF is searchable and copy-pasteable;
4. falls back per character run to a Latin font for symbols (→ ✔ ±) that
   Bengali fonts do not carry.

## Fonts

`tools/fonts_setup.py` looks for Noto Sans Bengali and DejaVu in the usual
system paths. If they are elsewhere:

```bash
export LIS_FONT_BN=/path/NotoSansBengali-Regular.ttf
export LIS_FONT_BN_BOLD=/path/NotoSansBengali-Bold.ttf
export LIS_FONT_LATIN=/path/DejaVuSans.ttf
export LIS_FONT_MONO=/path/DejaVuSansMono.ttf
```

Variable TTFs (the ones Google Fonts ships) are instantiated to static weights
automatically with `fontTools.varLib.instancer`.

## Editing the content

The Bengali text lives in `tools/build_guide_pdf.py` as calls like
`doc.h2(...)`, `doc.para(...)`, `doc.code(...)`, `doc.table(...)`. Inline
markup: `**bold**` and `` `code` ``. Change the prose, rerun the script, commit
both the script and the regenerated PDF.
