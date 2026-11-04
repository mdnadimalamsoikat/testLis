"""
pdfkit — a small dependency-free PDF writer with real Bengali shaping.

Why this exists
---------------
The project guide has to render Bengali text. Every "easy" route needs system
libraries that are not guaranteed to be present (pango/cairo, headless Chromium,
LaTeX). This module builds the PDF itself:

* **HarfBuzz** (``uharfbuzz``) shapes Bengali — conjuncts, reordered vowel
  signs, the lot — using real glyph ids.
* **fontTools** subsets the embedded fonts, so the output stays small.
* Text is written as glyph runs with a ToUnicode CMap: selectable and
  searchable, not a picture of text.
* A missing glyph (→, ✔, ±) falls back to a Latin font per character run,
  so mixed Bengali/English/symbol text never shows tofu.

Layout is a simple top-down flow: headings, paragraphs with an inline
``**bold**`` / ```code``` mini-syntax, bullet and numbered lists, tables,
dark code blocks and coloured callouts — enough for a technical guide.
"""

from __future__ import annotations

import io
import re
import zlib
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import uharfbuzz as hb
from fontTools import subset
from fontTools.ttLib import TTFont

PAGE_W, PAGE_H = 595.276, 841.890      # A4 in points
MM = 72.0 / 25.4


# ===========================================================================
# fonts
# ===========================================================================
class FontSource:
    """A TTF loaded twice: once for HarfBuzz metrics, once for subsetting."""

    def __init__(self, key: str, path: str, bold: bool = False):
        self.key = key
        self.path = path
        self.bold = bold
        self.reload()

    def reload(self) -> None:
        self.data = open(self.path, "rb").read()
        self.tt = TTFont(io.BytesIO(self.data), lazy=False)
        self.cmap: Dict[int, str] = self.tt.getBestCmap()
        self.upem: int = self.tt["head"].unitsPerEm
        self.hb_face = hb.Face(self.data)
        self.hb_font = hb.Font(self.hb_face)
        hhea = self.tt["hhea"]
        self.ascent = float(hhea.ascent)
        self.descent = float(abs(hhea.descent))
        self._order = self.tt.getGlyphOrder()

    def has(self, codepoint: int) -> bool:
        return codepoint in self.cmap

    @property
    def scale(self) -> float:
        return 1.0 / self.upem

    # ------------------------------------------------------------------ shaped
    def shape(self, text: str) -> Tuple[List[Tuple[int, float, float, float]], Dict[int, int]]:
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self.hb_font, buf)

        runs: List[Tuple[int, float, float, float]] = []
        gid_to_uni: Dict[int, int] = {}
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
            try:
                uni = ord(text[info.cluster])
            except IndexError:
                uni = 0x20
            gid_to_uni.setdefault(info.codepoint, uni)
            runs.append((info.codepoint, pos.x_advance * self.scale,
                         pos.x_offset * self.scale, pos.y_offset * self.scale))
        return runs, gid_to_uni


# ===========================================================================
# styles
# ===========================================================================
@dataclass
class Style:
    size: float = 10.0
    leading: float = 1.5
    color: Tuple[float, float, float] = (0.10, 0.13, 0.18)
    bold: bool = False
    mono: bool = False
    space_before: float = 0.0
    space_after: float = 0.0
    indent: float = 0.0
    align: str = "left"


def rgb(hex_color: str) -> Tuple[float, float, float]:
    value = hex_color.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore


# palette
INK = rgb("#0f172a")
BODY = rgb("#28323f")
MUTED = rgb("#5b6a7d")
ACCENT = rgb("#0b6b5f")
ACCENT_SOFT = rgb("#e6f4f1")
CODE_BG = rgb("#111a2b")
CODE_FG = rgb("#dbe6f2")
LINE = rgb("#dbe2ea")
PANEL = rgb("#f4f7fa")
WARN_BG = rgb("#fff7e6")
WARN = rgb("#a2620a")
OK_BG = rgb("#eaf7f1")
OK = rgb("#0f7a58")
DANGER_BG = rgb("#fdeef0")
DANGER = rgb("#a51f33")


@dataclass
class Segment:
    text: str
    style: Style


# ===========================================================================
# shaping + fallback
# ===========================================================================
class Shaper:
    def __init__(self, fonts: Dict[str, FontSource]):
        self.fonts = fonts
        self._cache: Dict[Tuple[str, str], List[Tuple[str, str]]] = {}
        self._shape_cache: Dict[Tuple[str, str], Tuple[list, dict]] = {}

    def primary(self, style: Style) -> FontSource:
        if style.mono:
            return self.fonts.get("mono-bold" if style.bold else "mono") or self.fonts["mono"]
        return self.fonts["sans-bold" if style.bold else "sans"]

    def fallback(self, style: Style) -> FontSource:
        return self.fonts.get("fallback-bold" if style.bold else "fallback") or self.fonts["fallback"]

    def chunks(self, text: str, style: Style) -> List[Tuple[str, str]]:
        """Split `text` into (font_key, piece) runs so no glyph is missing."""
        key = (self.primary(style).key, text)
        if key in self._cache:
            return self._cache[key]

        main = self.primary(style)
        # mono fonts rarely cover Bengali: fall back to the main text font first
        alt = self.fallback(style)
        secondary = self.fonts["sans"] if style.mono else None
        out: List[Tuple[str, str]] = []
        buf: List[str] = []
        current = main.key

        for ch in text:
            cp = ord(ch)
            if ch in "\n\t " or main.has(cp):
                chosen = main.key
            elif secondary is not None and secondary.has(cp):
                chosen = secondary.key
            elif alt.has(cp):
                chosen = alt.key
            else:
                chosen = main.key if main.has(0x20) else alt.key
            if chosen != current and buf:
                out.append((current, "".join(buf)))
                buf = []
            current = chosen
            buf.append(ch)
        if buf:
            out.append((current, "".join(buf)))

        self._cache[key] = out
        return out

    def shape(self, text: str, font_key: str) -> Tuple[list, dict]:
        key = (font_key, text)
        if key not in self._shape_cache:
            self._shape_cache[key] = self.fonts[font_key].shape(text)
        return self._shape_cache[key]

    def width(self, text: str, style: Style) -> float:
        total = 0.0
        for font_key, piece in self.chunks(text, style):
            runs, _ = self.shape(piece, font_key)
            total += sum(r[1] for r in runs) * style.size
        return total


# ===========================================================================
# document
# ===========================================================================
class Page:
    def __init__(self) -> None:
        self.ops: List[str] = []


class Document:
    def __init__(
        self,
        fonts: Dict[str, FontSource],
        margins: Tuple[float, float, float, float] = (20 * MM, 17 * MM, 18 * MM, 17 * MM),
        header: Optional[str] = None,
        footer: Optional[str] = None,
    ):
        self.fonts = fonts
        self.shaper = Shaper(fonts)
        self.top_margin, self.right_margin, self.bottom_margin, self.left_margin = margins
        self.pages: List[Page] = [Page()]
        self.x = self.left_margin
        self.y = PAGE_H - self.top_margin
        self.used_glyphs: Dict[str, set] = {key: set() for key in fonts}
        self.gid_to_uni: Dict[str, Dict[int, int]] = {key: {} for key in fonts}
        self.header_text = header
        self.footer_text = footer

    # ------------------------------------------------------------- geometry
    @property
    def content_width(self) -> float:
        return PAGE_W - self.left_margin - self.right_margin

    def ensure(self, height: float) -> None:
        if self.y - height < self.bottom_margin:
            self.new_page()

    def new_page(self) -> None:
        self.pages.append(Page())
        self.y = PAGE_H - self.top_margin
        self.x = self.left_margin

    def space(self, points: float) -> None:
        self.y -= points

    def page_break(self) -> None:
        self.new_page()

    # --------------------------------------------------------------- shapes
    @staticmethod
    def _fill(color: Tuple[float, float, float]) -> str:
        return f"{color[0]:.3f} {color[1]:.3f} {color[2]:.3f} rg"

    @staticmethod
    def _stroke(color: Tuple[float, float, float]) -> str:
        return f"{color[0]:.3f} {color[1]:.3f} {color[2]:.3f} RG"

    def rect(self, x: float, y: float, w: float, h: float,
             fill: Optional[Tuple[float, float, float]] = None,
             stroke: Optional[Tuple[float, float, float]] = None,
             radius: float = 0.0, line_width: float = 0.7) -> None:
        page = self.pages[-1]
        if radius > 0:
            k = 0.5523 * radius
            path = (
                f"{x + radius:.2f} {y:.2f} m "
                f"{x + w - radius:.2f} {y:.2f} l "
                f"{x + w - radius + k:.2f} {y:.2f} {x + w:.2f} {y + radius - k:.2f} {x + w:.2f} {y + radius:.2f} c "
                f"{x + w:.2f} {y + h - radius:.2f} l "
                f"{x + w:.2f} {y + h - radius + k:.2f} {x + w - radius + k:.2f} {y + h:.2f} {x + w - radius:.2f} {y + h:.2f} c "
                f"{x + radius:.2f} {y + h:.2f} l "
                f"{x + radius - k:.2f} {y + h:.2f} {x:.2f} {y + h - radius + k:.2f} {x:.2f} {y + h - radius:.2f} c "
                f"{x:.2f} {y + radius:.2f} l "
                f"{x:.2f} {y + radius - k:.2f} {x + radius - k:.2f} {y:.2f} {x + radius:.2f} {y:.2f} c h"
            )
        else:
            path = f"{x:.2f} {y:.2f} {w:.2f} {h:.2f} re"

        if fill is not None:
            page.ops.append(f"{self._fill(fill)} {path} f")
        if stroke is not None:
            page.ops.append(f"{self._stroke(stroke)} {line_width} w {path} S")

    def hline(self, x1: float, x2: float, y: float,
              color: Tuple[float, float, float] = LINE, width: float = 0.6) -> None:
        self.pages[-1].ops.append(
            f"{self._stroke(color)} {width} w {x1:.2f} {y:.2f} m {x2:.2f} {y:.2f} l S")

    # ----------------------------------------------------------------- text
    def draw_text(self, text: str, style: Style, x: float, baseline: float) -> float:
        """Draw shaped text; returns the advance width."""
        if not text:
            return 0.0
        page = self.pages[-1]
        size = style.size
        cursor = x
        for font_key, piece in self.shaper.chunks(text, style):
            runs, mapping = self.shaper.shape(piece, font_key)
            self.used_glyphs[font_key].update(r[0] for r in runs)
            self.gid_to_uni[font_key].update(mapping)

            parts = ["BT", self._fill(style.color), f"/{font_key} {size:.3f} Tf",
                     f"1 0 0 1 {cursor:.2f} {baseline:.2f} Tm"]
            for gid, advance, dx, dy in runs:
                if dx or dy:
                    parts.append(f"1 0 0 1 {cursor + dx * size:.3f} {baseline + dy * size:.3f} Tm")
                    parts.append(f"<{gid:04X}> Tj")
                    parts.append(f"1 0 0 1 {cursor:.3f} {baseline:.3f} Tm")
                else:
                    parts.append(f"<{gid:04X}> Tj")
                cursor += advance * size
            parts.append("ET")
            page.ops.append(" ".join(parts))
        return cursor - x

    # ------------------------------------------------------- inline markup
    @staticmethod
    def rich(text: str, base: Style, bold_color=None, code_scale: float = 0.92) -> List[Segment]:
        """Mini-markup: **bold** and `code` (Bengali and English both work)."""
        segments: List[Segment] = []
        for index, piece in enumerate(re.split(r"(\*\*[^*]+\*\*|`[^`]+`)", text)):
            if not piece:
                continue
            if piece.startswith("**") and piece.endswith("**"):
                style = Style(size=base.size, leading=base.leading,
                              color=bold_color or base.color, bold=True, mono=base.mono)
                segments.append(Segment(piece[2:-2], style))
            elif piece.startswith("`") and piece.endswith("`"):
                style = Style(size=base.size * code_scale, leading=base.leading,
                              color=rgb("#8a3ffc"), bold=False, mono=True)
                segments.append(Segment(piece[1:-1], style))
            else:
                segments.append(Segment(piece, base))
        return segments

    # ---------------------------------------------------------------- flow
    def _tokens(self, segments: Sequence[Segment]) -> List[Tuple[str, Style, bool]]:
        out: List[Tuple[str, Style, bool]] = []
        for seg in segments:
            for piece in re.split(r"(\s+)", seg.text):
                if piece:
                    out.append((piece, seg.style, piece.isspace()))
        return out

    def wrap(self, segments: Sequence[Segment], width: float) -> List[List[Tuple[str, Style, bool]]]:
        lines: List[List[Tuple[str, Style, bool]]] = [[]]
        used = 0.0
        for token, style, is_space in self._tokens(segments):
            tok_w = self.shaper.width(token, style)
            if is_space:
                if lines[-1]:
                    lines[-1].append((token, style, True))
                    used += tok_w
                continue
            if used + tok_w > width and lines[-1]:
                while lines[-1] and lines[-1][-1][2]:
                    sp_token, sp_style, _ = lines[-1].pop()
                    used -= self.shaper.width(sp_token, sp_style)
                lines.append([])
                used = 0.0
            lines[-1].append((token, style, False))
            used += tok_w
        return [ln for ln in lines if ln]

    def paragraph(self, segments: Sequence[Segment], base: Style,
                  x: Optional[float] = None, width: Optional[float] = None,
                  keep: bool = True) -> None:
        x = self.x if x is None else x
        width = self.content_width if width is None else width
        lines = self.wrap(segments, width)
        leading = base.size * base.leading

        if keep:
            self.ensure(min(leading * len(lines), self.content_height_guess()))
        self.y -= base.space_before

        for line in lines:
            self.ensure(leading)
            line_w = sum(self.shaper.width(t, s) for t, s, _ in line)
            offset = 0.0
            if base.align == "center":
                offset = max(0.0, (width - line_w) / 2)
            elif base.align == "right":
                offset = max(0.0, width - line_w)
            baseline = self.y - base.size * 0.80
            cursor = x + base.indent + offset
            for token, style, _ in line:
                cursor += self.draw_text(token, style, cursor, baseline)
            self.y -= leading

        self.y -= base.space_after

    def content_height_guess(self) -> float:
        return self.y - self.bottom_margin

    # ------------------------------------------------------------ components
    def h1(self, text: str, kicker: Optional[str] = None) -> None:
        if self.y < PAGE_H - self.top_margin - 1:      # not at the top of a page
            self.ensure(90)
            self.space(6)
        if kicker:
            self.paragraph([Segment(kicker.upper(), Style(size=7.5, color=ACCENT, bold=True))],
                           Style(size=7.5, space_after=2))
        self.paragraph([Segment(text, Style(size=17, bold=True, color=INK, leading=1.28))],
                       Style(size=17, leading=1.28, space_after=5))
        self.hline(self.left_margin, self.left_margin + self.content_width, self.y, LINE, 0.8)
        self.space(9)

    def h2(self, text: str, number: Optional[str] = None) -> None:
        # keep the heading with whatever follows it
        if self.y - 60 < self.bottom_margin:
            self.new_page()
        self.ensure(60)
        self.space(8)
        label = f"{number}  {text}" if number else text
        self.paragraph([Segment(label, Style(size=12.5, bold=True, color=INK, leading=1.35))],
                       Style(size=12.5, leading=1.35, space_after=4))

    def h3(self, text: str) -> None:
        if self.y - 70 < self.bottom_margin:
            self.new_page()
        self.ensure(40)
        self.space(5)
        self.paragraph([Segment(text, Style(size=10.8, bold=True, color=ACCENT, leading=1.4))],
                       Style(size=10.8, leading=1.4, space_after=3))

    def para(self, text: str, size: float = 10.0, color=BODY,
             space_after: float = 6.0, align: str = "left", indent: float = 0.0,
             width: Optional[float] = None, x: Optional[float] = None) -> None:
        base = Style(size=size, color=color, space_after=space_after, align=align, indent=indent)
        self.paragraph(self.rich(text, base), base, x=x, width=width)

    def list_item(self, marker: str, text: str, size: float = 10.0, color=BODY,
                  indent: float = 15.0, marker_color=ACCENT) -> None:
        base = Style(size=size, color=color, leading=1.5)
        segments = self.rich(text, base)
        lines = self.wrap(segments, self.content_width - indent)
        leading = size * base.leading
        self.ensure(min(leading * len(lines), self.content_height_guess()) + 4)
        for index, line in enumerate(lines):
            self.ensure(leading)
            baseline = self.y - size * 0.80
            if index == 0:
                self.draw_text(marker, Style(size=size, bold=True, color=marker_color),
                               self.left_margin + 1, baseline)
            cursor = self.left_margin + indent
            for token, style, _ in line:
                cursor += self.draw_text(token, style, cursor, baseline)
            self.y -= leading
        self.y -= 1.5

    def bullets(self, items: Sequence[str], size: float = 10.0, color=BODY,
                marker: str = "•", indent: float = 15.0) -> None:
        for item in items:
            self.list_item(marker, item, size=size, color=color, indent=indent)
        self.space(4)

    def numbered(self, items: Sequence[str], size: float = 10.0, color=BODY,
                 indent: float = 17.0, start: int = 1) -> None:
        for index, item in enumerate(items, start=start):
            self.list_item(f"{index}.", item, size=size, color=color, indent=indent)
        self.space(4)

    def code(self, text: str, size: float = 8.2, caption: Optional[str] = None) -> None:
        lines: List[str] = []
        max_chars = max(20, int((self.content_width - 22) / (size * 0.602)))
        for raw_line in text.strip("\n").split("\n"):
            while len(raw_line) > max_chars:
                lines.append(raw_line[:max_chars])
                raw_line = "    " + raw_line[max_chars:]
            lines.append(raw_line)

        leading = size * 1.42
        pad = 8.0

        # If the block fits on an empty page but not on this one, break first.
        # A caption stranded at the bottom of a page is worse than a gap.
        whole = len(lines) * leading + pad * 2 + 2 + (16 if caption else 0)
        usable = PAGE_H - self.top_margin - self.bottom_margin
        # (long blocks are allowed to split across pages instead of leaving a gap)
        if whole > (self.y - self.bottom_margin) and whole <= usable and len(lines) <= 46:
            self.new_page()

        if caption:
            self.space(3)
            self.paragraph([Segment(caption, Style(size=7.8, color=MUTED, bold=True))],
                           Style(size=7.8, space_after=2))

        index = 0
        while index < len(lines):
            available = self.y - self.bottom_margin - pad * 2
            fits = max(1, int(available // leading))
            chunk = lines[index:index + fits]
            block_h = len(chunk) * leading + pad * 2 + 2
            self.ensure(block_h)
            self.rect(self.left_margin, self.y - block_h, self.content_width, block_h,
                      fill=CODE_BG, radius=5)
            baseline = self.y - pad - size * 0.75
            for line in chunk:
                self.draw_text(line, Style(size=size, mono=True, color=CODE_FG),
                               self.left_margin + 10, baseline)
                baseline -= leading
            self.y -= block_h + 6
            index += fits
            if index < len(lines):
                self.new_page()
        self.space(2)

    def table(self, headers: Sequence[str], rows: Sequence[Sequence[str]],
              widths: Optional[Sequence[float]] = None, size: float = 8.4,
              header_bg: Tuple[float, float, float] = PANEL, zebra: bool = True,
              first_col_bold: bool = False) -> None:
        total = self.content_width
        if widths is None:
            widths = [total / len(headers)] * len(headers)
        else:
            scale = total / sum(widths)
            widths = [w * scale for w in widths]

        row_pad = 4.0
        line_h = size * 1.42

        def row_height(cells: Sequence[str]) -> float:
            lines = max(
                len(self.wrap(self.rich(cell, Style(size=size)), w - 8))
                for cell, w in zip(cells, widths)
            )
            return lines * line_h + row_pad

        def draw_row(cells: Sequence[str], y_top: float, height: float,
                     bold: bool, bg: Optional[Tuple[float, float, float]]) -> None:
            if bg is not None:
                self.rect(self.left_margin, y_top - height, total, height, fill=bg)
            x = self.left_margin
            for cell, w in zip(cells, widths):
                style = Style(size=size, color=INK if bold else BODY, bold=bold or (first_col_bold and x == self.left_margin))
                segments = self.rich(cell, style)
                inner: List[Segment] = []
                for seg in segments:
                    inner.append(Segment(seg.text, Style(size=seg.style.size, color=seg.style.color,
                                                         bold=seg.style.bold, mono=seg.style.mono)))
                base = Style(size=size, color=BODY)
                wrapped = self.wrap(inner, w - 8)
                baseline = y_top - row_pad - size * 0.9
                for line in wrapped:
                    cursor = x + 4
                    for token, token_style, _ in line:
                        cursor += self.draw_text(token, token_style, cursor, baseline)
                    baseline -= line_h
                if x != self.left_margin:
                    self.pages[-1].ops.append(
                        f"{self._stroke(LINE)} 0.5 w {x:.2f} {y_top - height:.2f} m {x:.2f} {y_top:.2f} l S")
                x += w

        header_height = row_height(headers)
        self.ensure(header_height + line_h * 2 + 6)
        draw_row(headers, self.y, header_height, True, header_bg)
        self.hline(self.left_margin, self.left_margin + total, self.y - header_height, LINE, 0.7)
        self.y -= header_height

        for index, row in enumerate(rows):
            height = row_height(row)
            if self.y - height < self.bottom_margin:
                self.new_page()
                draw_row(headers, self.y, header_height, True, header_bg)
                self.y -= header_height
            draw_row(row, self.y, height, False,
                     rgb("#ffffff") if not zebra or index % 2 == 0 else rgb("#fafbfc"))
            self.hline(self.left_margin, self.left_margin + total, self.y - height, LINE, 0.4)
            self.y -= height
        self.space(8)

    def callout(self, title: str, text: str, kind: str = "info") -> None:
        palette = {
            "info": (PANEL, ACCENT, INK),
            "ok": (OK_BG, OK, OK),
            "warn": (WARN_BG, WARN, WARN),
            "danger": (DANGER_BG, DANGER, DANGER),
        }
        bg, bar, title_color = palette.get(kind, palette["info"])
        base = Style(size=9.4, color=BODY, leading=1.5)
        segments = self.rich(text, base)
        inner_w = self.content_width - 24
        lines = self.wrap(segments, inner_w)
        title_lines = self.wrap([Segment(title, Style(size=9.4, bold=True))], inner_w) if title else []
        height = (len(lines) + len(title_lines)) * 9.4 * 1.5 + 16
        self.ensure(height + 4)
        self.rect(self.left_margin, self.y - height, self.content_width, height, fill=bg, radius=5)
        self.rect(self.left_margin, self.y - height, 3.0, height, fill=bar, radius=1.5)
        baseline = self.y - 12
        for line in title_lines:
            cursor = self.left_margin + 12
            for token, _, _ in line:
                cursor += self.draw_text(token, Style(size=9.4, bold=True, color=title_color),
                                         cursor, baseline)
            baseline -= 9.4 * 1.5
        for line in lines:
            cursor = self.left_margin + 12
            for token, style, _ in line:
                cursor += self.draw_text(token, style, cursor, baseline)
            baseline -= 9.4 * 1.5
        self.y -= height + 10

    def kv(self, pairs: Sequence[Tuple[str, str]], size: float = 9.4) -> None:
        for key, value in pairs:
            base = Style(size=size, color=BODY, space_after=2)
            self.paragraph(self.rich(f"**{key}** — {value}", base), base)

    # --------------------------------------------------------------- output
    def build(self) -> bytes:
        self._draw_chrome()
        return self._serialise()

    def _draw_chrome(self) -> None:
        total = len(self.pages)
        for index, page in enumerate(self.pages, start=1):
            saved = self.pages
            self.pages = [page]
            if self.header_text and index > 1:
                style = Style(size=7.6, color=MUTED)
                self.draw_text(self.header_text, style, self.left_margin, PAGE_H - 30)
                self.hline(self.left_margin, PAGE_W - self.right_margin, PAGE_H - 35, LINE, 0.5)
            if self.footer_text:
                label = self.footer_text.replace("{n}", str(index)).replace("{total}", str(total))
                style = Style(size=7.6, color=MUTED)
                width = self.shaper.width(label, style)
                self.draw_text(label, style, PAGE_W - self.right_margin - width, 26)
                self.hline(self.left_margin, PAGE_W - self.right_margin, 36, LINE, 0.5)
            self.pages = saved

    # ------------------------------------------------------------- writer
    def _subset_fonts(self) -> Dict[str, bytes]:
        out: Dict[str, bytes] = {}
        for key, font in self.fonts.items():
            gids = sorted(self.used_glyphs.get(key) or {0})
            order = font.tt.getGlyphOrder()
            names = [order[gid] for gid in gids if 0 <= gid < len(order)] or [order[0]]
            opts = subset.Options()
            opts.glyph_names = False
            opts.retain_gids = True      # content streams address glyphs by gid
            opts.notdef_outline = True
            opts.drop_tables += ["DSIG", "FFTM"]
            opts.layout_features = ["*"]
            opts.name_IDs = ["*"]
            sub = subset.Subsetter(options=opts)
            sub.populate(glyphs=names)
            sub.subset(font.tt)
            buffer = io.BytesIO()
            font.tt.save(buffer)
            out[key] = buffer.getvalue()
            font.reload()
        return out

    def _subset_widths(self, font: FontSource, gids: Iterable[int]) -> bytes:
        hmtx = font.tt["hmtx"].metrics
        order = font.tt.getGlyphOrder()
        scale = 1000.0 / font.upem
        parts = [f"{gid} [{int(hmtx[order[gid]][0] * scale)}]"
                 for gid in sorted(gids) if 0 <= gid < len(order)]
        return ("[" + " ".join(parts) + "]").encode()

    def _tounicode(self, key: str) -> bytes:
        lines = [
            "/CIDInit /ProcSet findresource begin",
            "12 dict begin",
            "begincmap",
            "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
            "/CMapName /Adobe-Identity-UCS def",
            "/CMapType 2 def",
            "1 begincodespacerange",
            "<0000> <FFFF>",
            "endcodespacerange",
        ]
        items = sorted((self.gid_to_uni.get(key) or {}).items())
        for start in range(0, len(items), 100):
            chunk = items[start:start + 100]
            lines.append(f"{len(chunk)} beginbfchar")
            lines += [f"<{gid:04X}> <{uni:04X}>" for gid, uni in chunk]
            lines.append("endbfchar")
        lines += ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]
        return "\n".join(lines).encode("ascii")

    def _serialise(self) -> bytes:
        font_bytes = self._subset_fonts()
        objects: List[bytes] = []

        def add(payload: bytes) -> int:
            objects.append(payload)
            return len(objects)

        catalog_id = add(b"")
        pages_id = add(b"")
        font_ids: Dict[str, int] = {}

        for key, data in font_bytes.items():
            font = self.fonts[key]
            descriptor_id = add(b"")
            file_id = add(b"<< /Length " + str(len(data)).encode() + b" /Length1 "
                          + str(len(data)).encode() + b" /Filter /FlateDecode >>\nstream\n"
                          + zlib.compress(data) + b"\nendstream")
            widths = self._subset_widths(font, self.used_glyphs[key] or {0})

            type0_id = len(objects) + 1
            cid_id = type0_id + 1
            tounicode_id = type0_id + 2
            font_ids[key] = type0_id

            add(b"<< /Type /Font /Subtype /Type0 /BaseFont /" + key.encode() +
                b" /Encoding /Identity-H /DescendantFonts [" + str(cid_id).encode() +
                b" 0 R] /ToUnicode " + str(tounicode_id).encode() + b" 0 R >>")
            add(b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /" + key.encode() +
                b" /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >>" +
                b" /FontDescriptor " + str(descriptor_id).encode() + b" 0 R" +
                b" /DW 1000 /W " + widths + b" /CIDToGIDMap /Identity >>")

            tounicode = self._tounicode(key)
            add(b"<< /Length " + str(len(tounicode)).encode() + b" /Filter /FlateDecode >>\nstream\n"
                + zlib.compress(tounicode) + b"\nendstream")

            scale = 1000.0 / font.upem
            head = font.tt["head"]
            bbox = " ".join(str(int(v * scale)) for v in (head.xMin, head.yMin, head.xMax, head.yMax))
            objects[descriptor_id - 1] = (
                b"<< /Type /FontDescriptor /FontName /" + key.encode() +
                b" /Flags 4 /FontBBox [" + bbox.encode() + b"] /ItalicAngle 0" +
                b" /Ascent " + str(int(font.ascent * scale)).encode() +
                b" /Descent " + str(int(-font.descent * scale)).encode() +
                b" /CapHeight " + str(int(font.ascent * 0.7 * scale)).encode() +
                b" /StemV 80 /FontFile2 " + str(file_id).encode() + b" 0 R >>"
            )

        font_res = b" ".join(f"/{k} {v} 0 R".encode() for k, v in font_ids.items())
        page_ids: List[int] = []
        for page in self.pages:
            stream = "\n".join(page.ops).encode("latin-1", errors="replace")
            content_id = add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
                             + stream + b"\nendstream")
            page_id = add(b"<< /Type /Page /Parent " + str(pages_id).encode() + b" 0 R /MediaBox [0 0 "
                          + f"{PAGE_W:.2f} {PAGE_H:.2f}".encode()
                          + b"] /Resources << /Font << " + font_res
                          + b" >> /ProcSet [/PDF /Text] >> /Contents "
                          + str(content_id).encode() + b" 0 R >>")
            page_ids.append(page_id)

        kids = b" ".join(str(pid).encode() + b" 0 R" for pid in page_ids)
        objects[pages_id - 1] = (b"<< /Type /Pages /Count " + str(len(page_ids)).encode()
                                 + b" /Kids [" + kids + b"] >>")
        objects[catalog_id - 1] = b"<< /Type /Catalog /Pages " + str(pages_id).encode() + b" 0 R >>"

        out = io.BytesIO()
        out.write(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0]
        for index, payload in enumerate(objects, start=1):
            offsets.append(out.tell())
            out.write(str(index).encode() + b" 0 obj\n" + payload + b"\nendobj\n")

        xref_at = out.tell()
        out.write(b"xref\n0 " + str(len(objects) + 1).encode() + b"\n")
        out.write(b"0000000000 65535 f \n")
        for offset in offsets[1:]:
            out.write(f"{offset:010d} 00000 n \n".encode())
        out.write(b"trailer\n<< /Size " + str(len(objects) + 1).encode() +
                  b" /Root " + str(catalog_id).encode() + b" 0 R >>\nstartxref\n" +
                  str(xref_at).encode() + b"\n%%EOF\n")
        return out.getvalue()
