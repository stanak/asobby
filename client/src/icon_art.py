"""asobby ブランドロゴ (Web favicon / トレイアイコン共通)。"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageOps

SIZE = 32
BADGE_RANKED = (90, 158, 255)
BADGE_CASUAL = (87, 192, 125)

MARK_BOX = (200, 290, 830, 550)
WORDMARK_BOX = (200, 290, 830, 690)

_mark_rgba: Image.Image | None = None
_wordmark_rgba: Image.Image | None = None


def _logo_source_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "assets" / "logo-source.png"
    return Path(__file__).resolve().parent.parent / "assets" / "logo-source.png"


def _strip_near_white(img: Image.Image, threshold: int = 250) -> Image.Image:
    """白背景を透明化する（ロゴ周辺のアンチエイリアスもソフトに処理）。"""
    img = img.convert("RGBA")
    px = img.load()
    w, h = img.size
    soft = 24
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            if r >= threshold and g >= threshold and b >= threshold:
                px[x, y] = (r, g, b, 0)
                continue
            min_rgb = min(r, g, b)
            if min_rgb >= threshold - soft:
                fade = int(255 * (threshold - min_rgb) / soft)
                px[x, y] = (r, g, b, min(a, max(0, fade)))
    return img


def _load_rgba(path: Path, box: tuple[int, int, int, int]) -> Image.Image:
    img = Image.open(path).convert("RGBA")
    cropped = img.crop(box)
    return _strip_near_white(cropped)


def _mark_image() -> Image.Image:
    global _mark_rgba
    if _mark_rgba is None:
        _mark_rgba = _load_rgba(_logo_source_path(), MARK_BOX)
    return _mark_rgba.copy()


def _wordmark_image() -> Image.Image:
    global _wordmark_rgba
    if _wordmark_rgba is None:
        _wordmark_rgba = _load_rgba(_logo_source_path(), WORDMARK_BOX)
    return _wordmark_rgba.copy()


def _style_mark(mark: Image.Image, accent: tuple[int, int, int]) -> Image.Image:
    if accent == (128, 128, 128):
        gray = ImageOps.grayscale(mark.convert("RGB")).convert("RGBA")
        alpha = mark.split()[3]
        gray.putalpha(alpha)
        return ImageEnhance.Brightness(gray).enhance(0.95)
    if accent == (219, 109, 40):
        tinted = mark.copy()
        overlay = Image.new("RGBA", tinted.size, accent + (70,))
        return Image.alpha_composite(tinted, overlay)
    return mark


def _fit_square(mark: Image.Image, size: int) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pad = max(1, round(size * 0.06))
    inner = size - pad * 2
    scaled = mark.copy()
    scaled.thumbnail((inner, inner), Image.Resampling.LANCZOS)
    x = (size - scaled.width) // 2
    y = (size - scaled.height) // 2
    canvas.paste(scaled, (x, y), scaled)
    return canvas


def draw_badge_dot(
    draw: ImageDraw.ImageDraw,
    size: int,
    x: float,
    y: float,
    color: tuple[int, int, int] = BADGE_RANKED,
) -> None:
    scale = size / SIZE
    cx = x * scale
    cy = y * scale
    outer = 6.75 * scale
    inner = 5.25 * scale
    shadow = 0.35 * scale
    draw.ellipse(
        (cx - outer - shadow, cy - outer, cx + outer + shadow, cy + outer + shadow),
        fill=(0, 0, 0, 170),
    )
    draw.ellipse(
        (cx - outer, cy - outer, cx + outer, cy + outer),
        fill=(255, 255, 255, 255),
    )
    draw.ellipse(
        (cx - inner, cy - inner, cx + inner, cy + inner),
        fill=color + (255,),
    )


def render_icon(
    size: int,
    *,
    accent: tuple[int, int, int] = (46, 160, 67),
    frame: tuple[int, int, int] | None = None,
    badges: dict[str, bool] | None = None,
) -> Image.Image:
    del frame  # 旧幾何学マーク互換。新ロゴでは未使用。
    mark = _style_mark(_mark_image(), accent)
    img = _fit_square(mark, size)
    draw = ImageDraw.Draw(img)
    next_badges = badges or {}
    if next_badges.get("casual"):
        draw_badge_dot(draw, size, 6.5, 6.5, BADGE_CASUAL)
    if next_badges.get("ranked"):
        draw_badge_dot(draw, size, 25.5, 6.5, BADGE_RANKED)
    return img


def render_wordmark(height: int = 40) -> Image.Image:
    wordmark = _wordmark_image()
    scale = height / wordmark.height
    width = max(1, round(wordmark.width * scale))
    return wordmark.resize((width, height), Image.Resampling.LANCZOS)


def write_favicon_svg(out_path: Path, png_path: Path) -> None:
    png_bytes = png_path.read_bytes()
    encoded = base64.b64encode(png_bytes).decode("ascii")
    out_path.write_text(
        "\n".join(
            [
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="asobby">',
                f'  <image href="data:image/png;base64,{encoded}" width="64" height="64"/>',
                "</svg>",
            ]
        ),
        encoding="utf-8",
    )


def write_static_assets(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    render_icon(32).save(out_dir / "favicon-32.png")
    render_icon(64).save(out_dir / "favicon-64.png")
    render_icon(192).save(out_dir / "apple-touch-icon.png")
    render_wordmark(40).save(out_dir / "logo-header.png")
    ico_images = [render_icon(s) for s in (32, 48, 64)]
    ico_images[0].save(
        out_dir / "favicon.ico",
        format="ICO",
        sizes=[(img.width, img.height) for img in ico_images],
        append_images=ico_images[1:],
    )
    write_favicon_svg(out_dir / "favicon.svg", out_dir / "favicon-64.png")


if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parents[2]
    static_dir = repo_root / "server" / "app" / "static"
    write_static_assets(static_dir)
    print(f"wrote icons to {static_dir}")
