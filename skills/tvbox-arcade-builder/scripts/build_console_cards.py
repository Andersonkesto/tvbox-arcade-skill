"""Generate rounded gradient console cards combining hardware photos and logos.

Example:
    python build_console_cards.py --devices-dir ./devices --logos-dir ./logos --out-dir ./shortnames
"""
import argparse
import os
import shutil
from PIL import Image, ImageDraw, ImageFilter

DEFAULT_PLATFORMS = {
    "snes":      ("snes.png",      "snes.png",      "#b81d24", "#56080c"),
    "gba":       ("gba.png",       "gba.png",       "#323cb0", "#101542"),
    "gbc":       ("gbc.png",       "gbc.png",       "#8c2240", "#450d1e"),
    "mame":      ("arcade.png",    "mame.png",      "#0d8ee0", "#02457a"),
    "nes":       ("nes.png",       "nes.png",       "#b03a34", "#5a1814"),
    "megadrive": ("megadrive.png", "megadrive.png", "#2356b5", "#102454"),
    "arcade":    ("arcade.png",    "arcade.png",    "#cc4d14", "#631f08"),
    "neogeo":    ("neogeo.png",    "neogeo.png",    "#baa038", "#5a4b14"),
    "psx":       ("psx.png",       "psx.png",       "#d96b08", "#783800"),
}


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def make_rounded_gradient(size: int, radius: int, top_hex: str, bottom_hex: str) -> Image.Image:
    r1, g1, b1 = hex_to_rgb(top_hex)
    r2, g2, b2 = hex_to_rgb(bottom_hex)

    grad = Image.new("RGBA", (size, size))
    px = grad.load()
    for y in range(size):
        t = y / max(1, size - 1)
        r = int(r1 + (r2 - r1) * t)
        g = int(g1 + (g2 - g1) * t)
        b = int(b1 + (b2 - b1) * t)
        for x in range(size):
            px[x, y] = (r, g, b, 255)

    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([size * 0.1, -size * 0.2, size * 0.9, size * 0.55], fill=(255, 255, 255, 38))
    glow = glow.filter(ImageFilter.GaussianBlur(max(1, int(size * 55 / 600))))
    grad = Image.alpha_composite(grad, glow)

    mask_2x = Image.new("L", (size * 2, size * 2), 0)
    md = ImageDraw.Draw(mask_2x)
    md.rounded_rectangle([0, 0, size * 2 - 1, size * 2 - 1], radius=radius * 2, fill=255)
    mask = mask_2x.resize((size, size), Image.LANCZOS)

    card = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    card.paste(grad, (0, 0), mask)

    border_2x = Image.new("RGBA", (size * 2, size * 2), (0, 0, 0, 0))
    bd = ImageDraw.Draw(border_2x)
    bd.rounded_rectangle(
        [3, 3, size * 2 - 4, size * 2 - 4],
        radius=max(1, radius * 2 - 3),
        outline=(255, 255, 255, 45),
        width=6,
    )
    border = border_2x.resize((size, size), Image.LANCZOS)
    return Image.alpha_composite(card, border)


def fit_and_shadow(
    img_path: str,
    size: int,
    max_w: int,
    max_h: int,
    center_y: int,
    shadow_blur: int,
    shadow_offset_y: int,
    shadow_alpha: int,
    canvas: Image.Image,
) -> Image.Image:
    im = Image.open(img_path).convert("RGBA")
    bbox = im.getbbox()
    if bbox:
        im = im.crop(bbox)
    scale = min(max_w / im.width, max_h / im.height)
    nw = max(1, int(round(im.width * scale)))
    nh = max(1, int(round(im.height * scale)))
    im = im.resize((nw, nh), Image.LANCZOS)

    x = (size - nw) // 2
    y = center_y - nh // 2

    alpha_ch = im.getchannel("A")
    sh_alpha = alpha_ch.point(lambda a: int(a * shadow_alpha / 255))
    shadow_src = Image.new("RGBA", (nw, nh), (0, 0, 0, 0))
    shadow_src.putalpha(sh_alpha)

    shadow_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shadow_layer.paste(shadow_src, (x, y + shadow_offset_y))
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(shadow_blur))

    fg_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    fg_layer.paste(im, (x, y))

    out = Image.alpha_composite(canvas, shadow_layer)
    return Image.alpha_composite(out, fg_layer)


def main():
    parser = argparse.ArgumentParser(description="Build rounded 600x600 console cards for Pegasus themes.")
    parser.add_argument("--devices-dir", required=True, help="Directory with console hardware PNGs")
    parser.add_argument("--logos-dir", required=True, help="Directory with white console logo PNGs")
    parser.add_argument("--out-dir", required=True, help="Output directory (e.g., theme/assets/shortnames)")
    parser.add_argument("--backup-dir", default="", help="Optional backup dir for existing icons")
    parser.add_argument("--size", type=int, default=600, help="Square card dimension in px (default: 600)")
    parser.add_argument(
        "--platforms",
        default=",".join(DEFAULT_PLATFORMS.keys()),
        help="Comma-separated platform shortnames to build",
    )
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    if args.backup_dir:
        os.makedirs(args.backup_dir, exist_ok=True)

    size = args.size
    radius = max(4, int(round(size * 76 / 600)))
    k = size / 600.0

    for short in [p.strip() for p in args.platforms.split(",") if p.strip()]:
        if short not in DEFAULT_PLATFORMS:
            print(f"SKIP unknown platform preset: {short}")
            continue
        dev_png, logo_png, top_hex, bot_hex = DEFAULT_PLATFORMS[short]
        dev_path = os.path.join(args.devices_dir, dev_png)
        logo_path = os.path.join(args.logos_dir, logo_png)
        if not os.path.exists(dev_path) or not os.path.exists(logo_path):
            print(f"SKIP {short}: missing {dev_path} or {logo_path}")
            continue

        out_path = os.path.join(args.out_dir, f"{short}.png")
        if args.backup_dir and os.path.exists(out_path):
            bak_path = os.path.join(args.backup_dir, f"{short}.png")
            if not os.path.exists(bak_path):
                shutil.copy2(out_path, bak_path)

        card = make_rounded_gradient(size, radius, top_hex, bot_hex)
        card = fit_and_shadow(
            dev_path, size,
            max_w=int(490 * k), max_h=int(345 * k), center_y=int(215 * k),
            shadow_blur=max(1, int(14 * k)), shadow_offset_y=int(12 * k), shadow_alpha=115,
            canvas=card,
        )
        card = fit_and_shadow(
            logo_path, size,
            max_w=int(460 * k), max_h=int(105 * k), center_y=int(495 * k),
            shadow_blur=max(1, int(6 * k)), shadow_offset_y=int(4 * k), shadow_alpha=140,
            canvas=card,
        )
        card.save(out_path, format="PNG", optimize=True)
        print(f"OK: {out_path} ({os.path.getsize(out_path)} bytes)")


if __name__ == "__main__":
    main()
