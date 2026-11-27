"""Safely patch boot logos inside a Rockchip RSCE `resource` partition dump.

- Reads the RSCE index table (`ENTR` records) to find logos by name
  (`logo.bmp` for U-Boot and `logo_kernel.bmp` for the kernel), patching all of
  them by default; falls back to a validated `BM` scan when no table is found.
- Supports uncompressed 24-bit and 8-bit (palettized) BMPs. The replacement is
  rendered into exactly the same byte size, header, orientation and padding.
- Refreshes per-entry SHA1/SHA256 hashes when the newer index layout carries them.
- `--list` / `--extract` inspect a dump without writing anything.

Never uses hardcoded offsets. Always keep the untouched backup next to the output.
"""
import argparse
import hashlib
import io
import os
import struct

try:
    from PIL import Image
except ImportError as exc:
    raise SystemExit("Missing dependency: Pillow. Install it with: pip install pillow") from exc

BLOCK = 512
SUPPORTED_BPP = (8, 24)


def bmp_row_stride(width: int, bpp: int = 24) -> int:
    """BMP scanlines are padded to 4-byte boundaries."""
    return ((abs(width) * bpp + 31) // 32) * 4


def parse_bmp_header(data: bytes, idx: int) -> dict | None:
    """Return BMP geometry if `data[idx:]` holds a plausible uncompressed 8/24-bit logo, else None."""
    if data[idx: idx + 2] != b"BM" or idx + 54 > len(data):
        return None
    size, = struct.unpack("<I", data[idx + 2: idx + 6])
    off_bits, = struct.unpack("<I", data[idx + 10: idx + 14])
    dib_size, = struct.unpack("<I", data[idx + 14: idx + 18])
    w, h = struct.unpack("<ii", data[idx + 18: idx + 26])
    bpp, = struct.unpack("<H", data[idx + 28: idx + 30])
    comp, = struct.unpack("<I", data[idx + 30: idx + 34])
    clr_used, = struct.unpack("<I", data[idx + 46: idx + 50])
    if not (dib_size >= 40 and off_bits >= 54 and 100 <= w <= 3840 and 100 <= abs(h) <= 2160):
        return None
    if bpp not in SUPPORTED_BPP or comp != 0:
        return None
    ncolors = 0
    if bpp == 8:
        ncolors = clr_used or 256
        if ncolors > 256 or 14 + dib_size + 4 * ncolors > off_bits:
            return None
    expected = off_bits + bmp_row_stride(w, bpp) * abs(h)
    if size not in (expected, expected + 2) or idx + size > len(data):
        return None
    return {
        "offset": idx, "size": size, "width": w, "height": h, "bpp": bpp,
        "off_bits": off_bits, "dib_size": dib_size, "ncolors": ncolors,
    }


def find_bmp_slots(data: bytes) -> list[dict]:
    """Scan for every non-overlapping valid BMP (fallback when the RSCE table is unreadable)."""
    slots: list[dict] = []
    idx = 0
    while True:
        idx = data.find(b"BM", idx)
        if idx == -1:
            return slots
        hdr = parse_bmp_header(data, idx)
        if hdr:
            hdr["name"] = f"bmp@0x{idx:x}"
            slots.append(hdr)
            idx += hdr["size"]
        else:
            idx += 1


def find_bmp_slot(data: bytes) -> tuple[int, int, int, int, int]:
    """Return (offset, bmp_size, width, signed_height, pixel_offset) of the first valid BMP."""
    slots = find_bmp_slots(data)
    if not slots:
        raise RuntimeError("No valid uncompressed 8/24-bit BMP logo found inside resource image")
    s = slots[0]
    return s["offset"], s["size"], s["width"], s["height"], s["off_bits"]


def parse_rsce_entries(data: bytes) -> list[dict]:
    """Parse the RSCE index table. Handles the classic layout (name, blk_offset, size) and the
    newer one that inserts a 32-byte hash + hash_size before blk_offset."""
    if data[:4] != b"RSCE" or len(data) < 16:
        return []
    tbl_offset = data[9] or 1
    entry_size = (data[10] or 1) * BLOCK
    entries: list[dict] = []
    pos = tbl_offset * BLOCK
    while pos + 304 <= len(data) and data[pos: pos + 4] == b"ENTR":
        name = data[pos + 4: pos + 260].split(b"\x00", 1)[0].decode("utf-8", errors="replace")
        layouts = (
            # (blk_offset field, size field, hash field, hash_size field)
            (260, 264, None, None),
            (296, 300, 260, 292),
        )
        for off_f, size_f, hash_f, hsize_f in layouts:
            blk, = struct.unpack("<I", data[pos + off_f: pos + off_f + 4])
            size, = struct.unpack("<I", data[pos + size_f: pos + size_f + 4])
            start = blk * BLOCK
            if size == 0 or start + size > len(data):
                continue
            if name.lower().endswith(".bmp") and data[start: start + 2] != b"BM":
                continue
            hash_size = 0
            if hsize_f is not None:
                hash_size, = struct.unpack("<I", data[pos + hsize_f: pos + hsize_f + 4])
            entries.append({
                "name": name, "offset": start, "size": size, "entry_pos": pos,
                "hash_pos": pos + hash_f if hash_f is not None else None, "hash_size": hash_size,
            })
            break
        pos += entry_size
    return entries


def find_logo_slots(data: bytes, wanted: list[str] | None = None) -> list[dict]:
    """Return patchable BMP slots, by RSCE entry name when possible."""
    entries = parse_rsce_entries(data)
    slots: list[dict] = []
    for e in entries:
        lname = e["name"].lower()
        if wanted:
            if lname not in wanted:
                continue
        elif not (lname.endswith(".bmp") and "logo" in lname):
            continue
        hdr = parse_bmp_header(data, e["offset"])
        if hdr is None:
            print(f"SKIP {e['name']}: unsupported BMP (RLE/compressed or not 8/24-bit)")
            continue
        hdr.update(name=e["name"], entry=e)
        slots.append(hdr)
    if entries:
        return slots
    # No index table: fall back to validated scan
    return find_bmp_slots(data)


def render_canvas(image_path: str, width: int, height: int, fit: str) -> Image.Image:
    src = Image.open(image_path).convert("RGB")
    if fit == "stretch":
        return src.resize((width, height), Image.LANCZOS)
    scale = min(width / src.width, height / src.height)
    nw, nh = max(1, round(src.width * scale)), max(1, round(src.height * scale))
    canvas = Image.new("RGB", (width, height), (0, 0, 0))
    canvas.paste(src.resize((nw, nh), Image.LANCZOS), ((width - nw) // 2, (height - nh) // 2))
    return canvas


def build_replacement_bmp(image_path: str, orig_bmp: bytes, hdr: dict, fit: str = "contain") -> bytes:
    """Render `image_path` into a BMP byte-identical in size/header/orientation to `orig_bmp`."""
    w, h, bpp = hdr["width"], hdr["height"], hdr["bpp"]
    abs_h = abs(h)
    stride = bmp_row_stride(w, bpp)
    out = bytearray(orig_bmp)  # keeps header, DIB extras and trailing pad bytes verbatim
    canvas = render_canvas(image_path, w, abs_h, fit)

    if bpp == 24:
        row_bytes = canvas.tobytes("raw", "BGR")
        row_len = w * 3
    else:
        ncolors = hdr["ncolors"]
        q = canvas.quantize(colors=ncolors, method=Image.Quantize.MEDIANCUT)
        pal = (q.getpalette() or [])[: ncolors * 3]
        pal += [0] * (ncolors * 3 - len(pal))
        pal_off = 14 + hdr["dib_size"]
        for i in range(ncolors):
            r, g, b = pal[i * 3: i * 3 + 3]
            out[pal_off + i * 4: pal_off + i * 4 + 4] = bytes((b, g, r, 0))
        row_bytes = q.tobytes()
        row_len = w

    rows = [row_bytes[y * row_len:(y + 1) * row_len] + b"\x00" * (stride - row_len) for y in range(abs_h)]
    if h > 0:  # bottom-up storage
        rows.reverse()
    pixels = b"".join(rows)
    start = hdr["off_bits"]
    out[start: start + len(pixels)] = pixels
    if len(out) != len(orig_bmp):
        raise RuntimeError(f"Generated BMP size {len(out)} != slot size {len(orig_bmp)}")
    return bytes(out)


def refresh_entry_hash(data: bytearray, slot: dict) -> str | None:
    entry = slot.get("entry")
    if not entry or entry["hash_pos"] is None or entry["hash_size"] not in (20, 32):
        return None
    content = bytes(data[entry["offset"]: entry["offset"] + entry["size"]])
    algo = "sha1" if entry["hash_size"] == 20 else "sha256"
    digest = hashlib.new(algo, content).digest()
    data[entry["hash_pos"]: entry["hash_pos"] + 32] = digest.ljust(32, b"\x00")
    return algo


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def describe(slot: dict) -> str:
    orient = "top-down" if slot["height"] < 0 else "bottom-up"
    return (
        f"{slot['name']}: offset {slot['offset']} {slot['width']}x{abs(slot['height'])} "
        f"{slot['bpp']}bpp {orient} ({slot['size']} bytes)"
    )


def main():
    parser = argparse.ArgumentParser(description="Safely patch boot logos inside a Rockchip resource.img dump.")
    parser.add_argument("--resource-in", required=True, help="Backed-up resource.img pulled from device")
    parser.add_argument("--image", help="Replacement image (PNG/JPG/BMP)")
    parser.add_argument("--resource-out", help="Output patched resource image path")
    parser.add_argument(
        "--entries", default="",
        help="Comma-separated RSCE entry names to patch (default: every *logo*.bmp, e.g. logo.bmp,logo_kernel.bmp)",
    )
    parser.add_argument("--fit", choices=("contain", "stretch"), default="contain",
                        help="contain = keep aspect ratio on black (default); stretch = fill the slot")
    parser.add_argument("--list", action="store_true", help="List RSCE entries and logo slots, write nothing")
    parser.add_argument("--extract", default="", help="Directory to save current logos as PNG previews")
    args = parser.parse_args()

    with open(args.resource_in, "rb") as f:
        res_data = bytearray(f.read())

    if res_data[:4] != b"RSCE":
        raise RuntimeError(f"Invalid Rockchip resource header: {res_data[:4]!r} (expected b'RSCE')")
    print(f"Input  {args.resource_in}: {len(res_data)} bytes sha256={sha256_hex(res_data)}")

    wanted = [n.strip().lower() for n in args.entries.split(",") if n.strip()] or None
    slots = find_logo_slots(bytes(res_data), wanted)

    if args.list:
        for e in parse_rsce_entries(bytes(res_data)):
            print(f"  ENTR {e['name']:<32} offset={e['offset']:<9} size={e['size']}")
    for s in slots:
        print(f"Detected BMP slot {describe(s)}")
    if not slots:
        raise RuntimeError("No patchable logo BMP found (use --list to inspect entries)")

    if args.extract:
        os.makedirs(args.extract, exist_ok=True)
        for s in slots:
            png_path = os.path.join(args.extract, os.path.splitext(os.path.basename(s["name"]))[0] + ".png")
            with Image.open(io.BytesIO(bytes(res_data[s["offset"]: s["offset"] + s["size"]]))) as im:
                im.convert("RGB").save(png_path)
            print(f"Extracted {png_path}")

    if (args.list or args.extract) and not (args.image and args.resource_out):
        return
    if not args.image or not args.resource_out:
        parser.error("--image and --resource-out are required to patch")

    for s in slots:
        orig = bytes(res_data[s["offset"]: s["offset"] + s["size"]])
        res_data[s["offset"]: s["offset"] + s["size"]] = build_replacement_bmp(args.image, orig, s, args.fit)
        algo = refresh_entry_hash(res_data, s)
        print(f"Patched {s['name']}" + (f" (refreshed {algo} entry hash)" if algo else ""))

    out_dir = os.path.dirname(os.path.abspath(args.resource_out))
    os.makedirs(out_dir, exist_ok=True)
    with open(args.resource_out, "wb") as f:
        f.write(res_data)
    print(f"OK: wrote patched resource image {args.resource_out} ({len(res_data)} bytes sha256={sha256_hex(res_data)})")
    print("Verify the size matches the backup byte-for-byte before flashing with dd.")


if __name__ == "__main__":
    main()
