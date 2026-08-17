#!/usr/bin/env python3
"""Generate the menu icons under resources/media/.

Kodi only falls back to DefaultVideo.png when an item leaves its `icon` art key
empty, so every menu entry needs a real image. Rather than ship the same addon
logo thirteen times, each entry gets a distinct tile in one of the Immich logo
colours with a white glyph.

Rasterising uses qlmanage, which is macOS-only. The generated PNGs are
committed, so this only needs running when an icon changes.
"""

import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "resources" / "media"
SIZE = 256

# Sampled from the five petals of the Immich mark, plus neutrals for the
# utility entries that should not compete with the content sections.
COLOURS = {
    "blue": "#1E83F7",
    "green": "#17B45A",
    "red": "#F5352B",
    "amber": "#FFB400",
    "pink": "#EE7BB6",
    "violet": "#7C5CE6",
    "teal": "#0FB5A6",
    "orange": "#F5852B",
    "rose": "#E0417B",
    "indigo": "#4250AF",
    "sky": "#2AA5D8",
    "slate": "#55607A",
    "purple": "#8E44AD",
    "gray": "#6B7280",
}

STROKE = 'fill="none" stroke="#fff" stroke-width="16" stroke-linecap="round" stroke-linejoin="round"'


def gear_path(cx=128, cy=128, r_out=94, r_in=70, teeth=8):
    """Build a gear outline as an SVG path.

    Drawn rather than hand-authored because the tooth angles are tedious to get
    evenly spaced by hand.
    """
    step = 360.0 / teeth
    points = []
    for index in range(teeth):
        centre = index * step
        for radius, angle in (
            (r_out, centre - 12),
            (r_out, centre + 12),
            (r_in, centre + 18),
            (r_in, centre + step - 18),
        ):
            radians = math.radians(angle)
            points.append((cx + radius * math.cos(radians), cy + radius * math.sin(radians)))
    body = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f} {y:.1f}" for i, (x, y) in enumerate(points))
    return body + " Z"


GLYPHS = {
    # Calendar: the timeline is browsed by month.
    "timeline": ("blue", f'''
        <rect x="52" y="74" width="152" height="126" rx="16" {STROKE}/>
        <path d="M52 116 L204 116" {STROKE}/>
        <path d="M96 56 L96 92" {STROKE}/>
        <path d="M160 56 L160 92" {STROKE}/>
        <circle cx="94" cy="146" r="9" fill="#fff"/>
        <circle cx="128" cy="146" r="9" fill="#fff"/>
        <circle cx="162" cy="146" r="9" fill="#fff"/>
        <circle cx="94" cy="176" r="9" fill="#fff"/>
        <circle cx="128" cy="176" r="9" fill="#fff"/>'''),

    "photos": ("green", f'''
        <rect x="52" y="66" width="152" height="124" rx="18" {STROKE}/>
        <circle cx="94" cy="106" r="13" fill="#fff"/>
        <path d="M66 178 L104 134 L134 164 L158 142 L190 178 Z" fill="#fff"/>'''),

    "videos": ("red", f'''
        <rect x="48" y="74" width="160" height="108" rx="16" {STROKE}/>
        <path d="M112 104 L160 128 L112 152 Z" fill="#fff"/>'''),

    # Three offset cards read as a stack at small sizes.
    "albums": ("amber", '''
        <rect x="84" y="58" width="116" height="86" rx="14" fill="#fff"/>
        <rect x="70" y="82" width="132" height="94" rx="14" fill="#fff" stroke="#FFB400" stroke-width="10"/>
        <rect x="56" y="106" width="148" height="96" rx="16" fill="#fff" stroke="#FFB400" stroke-width="10"/>'''),

    "favourites": ("pink", '''
        <path d="M128 192 C128 192 58 148 58 106 C58 82 77 66 98 66 C112 66 123 74 128 85
                 C133 74 144 66 158 66 C179 66 198 82 198 106 C198 148 128 192 128 192 Z" fill="#fff"/>'''),

    "people": ("violet", '''
        <circle cx="128" cy="98" r="34" fill="#fff"/>
        <path d="M70 202 C70 162 96 142 128 142 C160 142 186 162 186 202 Z" fill="#fff"/>'''),

    "places": ("teal", '''
        <path d="M128 56 C99 56 76 79 76 108 C76 148 128 202 128 202 C128 202 180 148 180 108
                 C180 79 157 56 128 56 Z" fill="#fff"/>
        <circle cx="128" cy="107" r="20" fill="#0FB5A6"/>'''),

    "tags": ("orange", '''
        <path d="M148 52 L204 52 L204 108 L114 198 L58 142 Z" fill="#fff"
              stroke="#fff" stroke-width="12" stroke-linejoin="round"/>
        <circle cx="180" cy="76" r="13" fill="#F5852B"/>'''),

    # A sparkle reads as "resurfaced memory" better than a clock does.
    "memories": ("rose", '''
        <path d="M116 50 C124 96 138 110 184 118 C138 126 124 140 116 186
                 C108 140 94 126 48 118 C94 110 108 96 116 50 Z" fill="#fff"/>
        <path d="M192 158 C196 178 200 182 220 186 C200 190 196 194 192 214
                 C188 194 184 190 164 186 C184 182 188 178 192 158 Z" fill="#fff"/>'''),

    "search": ("indigo", f'''
        <circle cx="116" cy="116" r="46" {STROKE}/>
        <path d="M150 150 L196 196" {STROKE}/>'''),

    "random": ("sky", f'''
        <path d="M52 96 L92 96 C118 96 134 160 160 160 L196 160" {STROKE}/>
        <path d="M52 160 L92 160 C118 160 134 96 160 96 L196 96" {STROKE}/>
        <path d="M176 78 L200 96 L176 114 Z" fill="#fff"/>
        <path d="M176 142 L200 160 L176 178 Z" fill="#fff"/>'''),

    "settings": ("slate", f'''
        <path d="{gear_path()}" fill="#fff"/>
        <circle cx="128" cy="128" r="30" fill="#55607A"/>'''),

    # Paging affordance at the end of a long listing.
    "next": ("gray", '''
        <path d="M96 60 L164 128 L96 196" fill="none" stroke="#fff" stroke-width="24"
              stroke-linecap="round" stroke-linejoin="round"/>'''),
}


def svg_for(name: str) -> str:
    colour_key, glyph = GLYPHS[name]
    fill = COLOURS[colour_key]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{SIZE}" height="{SIZE}" '
        f'viewBox="0 0 256 256">\n'
        f'  <rect width="256" height="256" rx="52" fill="{fill}"/>\n'
        f'{glyph}\n</svg>\n'
    )


def main():
    if not shutil.which("qlmanage"):
        sys.exit("qlmanage not found — this generator is macOS-only")
    OUT.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as workdir:
        work = Path(workdir)
        for name in GLYPHS:
            (work / f"{name}.svg").write_text(svg_for(name))
        subprocess.run(
            ["qlmanage", "-t", "-s", str(SIZE), "-o", str(work)]
            + [str(work / f"{name}.svg") for name in GLYPHS],
            capture_output=True,
            check=False,
        )
        missing = []
        for name in GLYPHS:
            rendered = work / f"{name}.svg.png"
            if rendered.exists():
                shutil.copyfile(rendered, OUT / f"{name}.png")
            else:
                missing.append(name)
        if missing:
            sys.exit(f"qlmanage failed to render: {', '.join(missing)}")

    print(f"wrote {len(GLYPHS)} icons to {OUT}")


if __name__ == "__main__":
    main()
