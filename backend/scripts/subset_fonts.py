"""Subset the DejaVu faces we ship with the exporter.

fpdf2's core fonts are strictly Latin-1: an en-dash, a curly quote, a real
bullet and every icon raise FPDFUnicodeEncodingException. Embedding a font
is the only way around that, and DejaVu already carries the icons the
reference resumes use.

Full DejaVu is ~740KB a face, ~4MB for the family -- too much to vendor for
a glyph repertoire a resume will never leave. Subset to the ranges we
actually render and each face drops to ~55KB.

Run: python scripts/subset_fonts.py <source-dir>
"""

import sys
from pathlib import Path

from fontTools import subset

FACES = [
    "DejaVuSans.ttf",
    "DejaVuSans-Bold.ttf",
    "DejaVuSans-Oblique.ttf",
    "DejaVuSans-BoldOblique.ttf",
    "DejaVuSerif.ttf",
    "DejaVuSerif-Bold.ttf",
    "DejaVuSerif-Italic.ttf",
    "DejaVuSerif-BoldItalic.ttf",
]

#: Latin-1 + Latin Extended-A covers European names, which a resume will
#: have. The rest is punctuation and the symbols the templates draw.
CODEPOINTS = (
    list(range(0x20, 0x180))
    + [
        0x2013, 0x2014,          # en/em dash
        0x2018, 0x2019,          # curly single quotes
        0x201C, 0x201D,          # curly double quotes
        0x2022, 0x00B7, 0x25CF,  # bullets
        0x2026,                  # ellipsis
        0x20AC, 0x00A3, 0x00A5,  # currency
        0x2709, 0x260E, 0x2706,  # envelope, phone
        0x2713, 0x2714,          # check marks
        0x2605, 0x2606,          # stars
        0x2302, 0x1F4CD,         # house, pin
        0x00A9, 0x00AE, 0x2122,  # legal marks
    ]
)


def main(src_dir: str) -> None:
    out = Path(__file__).resolve().parent.parent / "app" / "assets" / "fonts"
    out.mkdir(parents=True, exist_ok=True)
    total = 0
    for face in FACES:
        src = Path(src_dir) / face
        if not src.exists():
            print(f"  MISSING {face}")
            continue
        opts = subset.Options()
        opts.layout_features = ["*"]
        opts.notdef_outline = True
        opts.drop_tables += ["FFTM"]
        font = subset.load_font(str(src), opts)
        sub = subset.Subsetter(options=opts)
        sub.populate(unicodes=CODEPOINTS)
        sub.subset(font)
        dest = out / face
        subset.save_font(font, str(dest), opts)
        size = dest.stat().st_size
        total += size
        print(f"  {face:28} {src.stat().st_size // 1024:4d} KB -> {size // 1024:3d} KB")
    print(f"  total vendored: {total // 1024} KB")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/usr/share/fonts/truetype/dejavu")
