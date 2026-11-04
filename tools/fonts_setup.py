"""
Locate the fonts the PDF generator needs.

Bengali needs a shaping-capable font (Noto Sans Bengali). Latin/symbols come
from whichever system font is available; if none of the usual paths match,
pass explicit paths through the environment:

    LIS_FONT_BN=/path/NotoSansBengali-Regular.ttf
    LIS_FONT_BN_BOLD=/path/NotoSansBengali-Bold.ttf
    LIS_FONT_LATIN=/path/DejaVuSans.ttf
    LIS_FONT_MONO=/path/DejaVuSansMono.ttf

Download links (Google Fonts, variable TTF):
    https://raw.githubusercontent.com/google/fonts/main/ofl/notosansbengali/NotoSansBengali%5Bwdth%2Cwght%5D.ttf
The generator automatically instantiates the variable font to a static weight
with fontTools, so the file above works as-is.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Optional

from pdfkit import FontSource

CANDIDATES = {
    "bn": [
        "/tmp/NotoSansBengali-Regular-static.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansBengali-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansBengali[wdth,wght].ttf",
        os.path.expanduser("~/.fonts/NotoSansBengali-Regular.ttf"),
    ],
    "bn-bold": [
        "/tmp/NotoSansBengali-Bold-static.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansBengali-Bold.ttf",
        os.path.expanduser("~/.fonts/NotoSansBengali-Bold.ttf"),
    ],
    "latin": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
    ],
    "latin-bold": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
    ],
    "mono": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        "C:/Windows/Fonts/consola.ttf",
    ],
    "mono-bold": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
        "C:/Windows/Fonts/consolab.ttf",
    ],
}


def find(slot: str, env: Optional[str] = None) -> str:
    if env and os.environ.get(env):
        return os.environ[env]
    for path in CANDIDATES[slot]:
        if Path(path).exists():
            return path
    raise FileNotFoundError(
        f"font for '{slot}' not found — set {env or 'LIS_FONT_*'} or install Noto Sans Bengali"
    )


def instantiate_static(src: str, dest: str, weight: int = 400) -> str:
    """Turn a variable TTF into a static weight so HarfBuzz uses it directly."""
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont

    font = TTFont(src)
    if "fvar" not in font:
        return src
    instance = instantiateVariableFont(font, {"wght": weight, "wdth": 100})
    instance.save(dest)
    return dest


def build_fonts(workdir: Path) -> dict[str, FontSource]:
    """Load every font the document needs."""
    workdir.mkdir(parents=True, exist_ok=True)

    bn = find("bn", "LIS_FONT_BN")
    bn_bold = os.environ.get("LIS_FONT_BN_BOLD") or (CANDIDATES["bn-bold"][0]
                                                     if Path(CANDIDATES["bn-bold"][0]).exists() else None)
    if bn_bold is None:
        # derive a bold instance from the variable font if we can
        try:
            bn_bold = instantiate_static(bn, str(workdir / "bn-bold.ttf"), 700)
        except Exception:
            bn_bold = bn

    latin = find("latin", "LIS_FONT_LATIN")
    latin_bold = os.environ.get("LIS_FONT_LATIN_BOLD") or next(
        (p for p in CANDIDATES["latin-bold"] if Path(p).exists()), latin)
    mono = find("mono", "LIS_FONT_MONO")
    mono_bold = next((p for p in CANDIDATES["mono-bold"] if Path(p).exists()), mono)

    return {
        # Bengali-capable font also carries Latin, so it is the default text font
        "sans": FontSource("sans", bn),
        "sans-bold": FontSource("sans-bold", bn_bold, bold=True),
        # symbol fallback (→ ✔ ≤) that Bengali fonts do not cover
        "fallback": FontSource("fallback", latin),
        "fallback-bold": FontSource("fallback-bold", latin_bold, bold=True),
        "mono": FontSource("mono", mono),
        "mono-bold": FontSource("mono-bold", mono_bold, bold=True),
    }
