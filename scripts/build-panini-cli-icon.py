"""Build the PANINI-CLI icon from the canonical standalone CLI glyph.

The source of truth is assets/panini-cli.png. Do not compose this from the
desktop PANINI icon and do not add a terminal badge: the CLI product icon is
the standalone gold PANINI glyph.
"""

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets/panini-cli.png"
PNG = ROOT / "assets/panini-cli-64.png"
ICO = ROOT / "assets/panini-cli.ico"

image = Image.open(SOURCE).convert("RGBA").resize((256, 256), Image.Resampling.LANCZOS)
image.resize((64, 64), Image.Resampling.LANCZOS).save(PNG)
image.save(
    ICO, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
)
