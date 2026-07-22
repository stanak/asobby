"""asobby 幾何学マーク (Web favicon / トレイアイコン共通)。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 32
BG = (23, 28, 36)
FRAME_DEFAULT = (54, 69, 89)
ACCENT_DEFAULT = (106, 176, 243)
CORE = (230, 237, 245)
BADGE_RANKED = (90, 158, 255)
BADGE_CASUAL = (87, 192, 125)

HEX_POINTS = (
    (16.0, 3.5),
    (26.5, 10.0),
    (26.5, 22.0),
    (16.0, 28.5),
    (5.5, 22.0),
    (5.5, 10.0),
)
LEFT_ARROW = ((8.5, 16.0), (13.0, 12.2), (13.0, 19.8))
RIGHT_ARROW = ((23.5, 16.0), (19.0, 12.2), (19.0, 19.8))
CORE_HALF = 2.35


def _scale(value: float, size: int) -> float:
    return value * size / SIZE


def _scale_point(x: float, y: float, size: int) -> tuple[float, float]:
    return (_scale(x, size), _scale(y, size))


def _hex_points(size: int) -> list[tuple[float, float]]:
    return [_scale_point(x, y, size) for x, y in HEX_POINTS]


def _diamond_points(size: int) -> list[tuple[float, float]]:
    cx = _scale(16.0, size)
    cy = _scale(16.0, size)
    r = _scale(CORE_HALF, size)
    return [
        (cx, cy - r),
        (cx + r, cy),
        (cx, cy + r),
        (cx - r, cy),
    ]


def draw_asobby_mark(
    draw: ImageDraw.ImageDraw,
    size: int,
    *,
    accent: tuple[int, int, int] = ACCENT_DEFAULT,
    frame: tuple[int, int, int] | None = None,
    core: tuple[int, int, int] = CORE,
    bg: tuple[int, int, int] = BG,
) -> None:
    frame_color = frame or FRAME_DEFAULT
    radius = max(2, round(_scale(7.0, size)))
    stroke = max(1, round(_scale(1.4, size)))

    draw.rounded_rectangle(
        (0, 0, size - 1, size - 1),
        radius=radius,
        fill=bg + (255,),
    )

    hex_pts = _hex_points(size)
    draw.line(hex_pts + [hex_pts[0]], fill=frame_color + (255,), width=stroke, joint="curve")

    left = [_scale_point(x, y, size) for x, y in LEFT_ARROW]
    right = [_scale_point(x, y, size) for x, y in RIGHT_ARROW]
    draw.polygon(left, fill=accent + (255,))
    draw.polygon(right, fill=accent + (255,))
    draw.polygon(_diamond_points(size), fill=core + (255,))


def draw_badge_dot(
    draw: ImageDraw.ImageDraw,
    size: int,
    x: float,
    y: float,
    color: tuple[int, int, int] = BADGE_RANKED,
) -> None:
    cx, cy = _scale_point(x, y, size)
    outer = _scale(6.75, size)
    inner = _scale(5.25, size)
    shadow = _scale(0.35, size)
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
    accent: tuple[int, int, int] = ACCENT_DEFAULT,
    frame: tuple[int, int, int] | None = None,
    badges: dict[str, bool] | None = None,
) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw_asobby_mark(draw, size, accent=accent, frame=frame)
    next_badges = badges or {}
    if next_badges.get("casual"):
        draw_badge_dot(draw, size, 6.5, 6.5, BADGE_CASUAL)
    if next_badges.get("ranked"):
        draw_badge_dot(draw, size, 25.5, 6.5, BADGE_RANKED)
    return img


def write_static_assets(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    render_icon(32).save(out_dir / "favicon-32.png")
    render_icon(64).save(out_dir / "favicon-64.png")
    render_icon(192).save(out_dir / "apple-touch-icon.png")
    ico_images = [render_icon(s) for s in (32, 48, 64)]
    ico_images[0].save(
        out_dir / "favicon.ico",
        format="ICO",
        sizes=[(img.width, img.height) for img in ico_images],
        append_images=ico_images[1:],
    )


if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parents[2]
    static_dir = repo_root / "server" / "app" / "static"
    write_static_assets(static_dir)
    print(f"wrote icons to {static_dir}")
