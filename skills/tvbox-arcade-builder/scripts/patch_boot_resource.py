"""Dynamically patch the embedded 24-bit BMP boot logo inside a Rockchip RSCE
resource partition image without relying on hardcoded offsets.
"""
import argparse
import io
import os
import struct

try:
    from PIL import Image
except ImportError as exc:
    raise SystemExit("Missing dependency: Pillow. Install it with: pip install pillow") from exc


def bmp_row_stride(width: int) -> int:
    """24-bit BMP scanlines are padded to 4-byte boundaries."""
    return ((abs(width) * 3 + 3) // 4) * 4


def find_bmp_slot(data: bytes) -> tuple[int, int, int, int, int]:
    """Return (offset, bmp_size, width, signed_height, pixel_offset) of an uncompressed 24-bit BMP."""
    idx = 0
    while True:
        idx = data.find(b"BM", idx)
        if idx == -1:
            break
        if idx + 54 <= len(data):
            size, = struct.unpack("<I", data[idx + 2 : idx + 6])
            bf_off_bits, = struct.unpack("<I", data[idx + 10 : idx + 14])
            dib_size, = struct.unpack("<I", data[idx + 14 : idx + 18])
            w, h = struct.unpack("<ii", data[idx + 18 : idx + 26])
            bpp, = struct.unpack("<H", data[idx + 28 : idx + 30])
            comp, = struct.unpack("<I", data[idx + 30 : idx + 34])
            stride = bmp_row_stride(w)
            expected = bf_off_bits + stride * abs(h)
            if (
                dib_size >= 40
                and bf_off_bits >= 54
                and 100 <= w <= 3840
                and 100 <= abs(h) <= 2160
                and bpp == 24
                and comp == 0
                and size in (expected, expected + 2)
                and idx + size <= len(data)
            ):
                return idx, size, w, h, bf_off_bits
        idx += 1
    raise RuntimeError("No valid uncompressed 24-bit BMP logo found inside resource image")


def build_matching_bmp(image_path: str, width: int, signed_height: int, target_size: int, bf_off_bits: int) -> bytes:
    """Render replacement image to an exact-size 24-bit BMP matching target orientation and padding."""
    abs_h = abs(signed_height)
    im = Image.open(image_path).convert("RGB").resize((width, abs_h), Image.LANCZOS)

    buf = io.BytesIO()
    im.save(buf, format="BMP")
    raw = bytearray(buf.getvalue())

    if len(raw) < 54:
        raise RuntimeError("Pillow produced an invalid BMP payload")

    # Preserve top-down orientation (negative height) if the original Rockchip logo used it
    if signed_height < 0:
        stride = bmp_row_stride(width)
        src_off, = struct.unpack("<I", raw[10:14])
        pixels = raw[src_off : src_off + stride * abs_h]
        flipped = bytearray()
        for row in range(abs_h - 1, -1, -1):
            start = row * stride
            flipped.extend(pixels[start : start + stride])
        raw[src_off : src_off + len(flipped)] = flipped
        raw[22:26] = struct.pack("<i", signed_height)

    # Adjust header offset / trailing padding bytes if original BMP had extra header or 2-byte pad
    src_off, = struct.unpack("<I", raw[10:14])
    if bf_off_bits > src_off:
        extra = bf_off_bits - src_off
        raw = raw[:src_off] + (b"\x00" * extra) + raw[src_off:]
        raw[10:14] = struct.pack("<I", bf_off_bits)

    if len(raw) + 2 == target_size:
        raw.extend(b"\x00\x00")

    raw[2:6] = struct.pack("<I", len(raw))

    if len(raw) != target_size:
        raise RuntimeError(f"Generated BMP size {len(raw)} != target slot size {target_size}")
    return bytes(raw)


def main():
    parser = argparse.ArgumentParser(description="Safely patch boot logo inside a Rockchip resource.img dump.")
    parser.add_argument("--resource-in", required=True, help="Backed-up resource.img pulled from device")
    parser.add_argument("--image", required=True, help="Replacement image (PNG/JPG/BMP)")
    parser.add_argument("--resource-out", required=True, help="Output patched resource image path")
    args = parser.parse_args()

    with open(args.resource_in, "rb") as f:
        res_data = bytearray(f.read())

    if res_data[:4] != b"RSCE":
        raise RuntimeError(f"Invalid Rockchip resource header: {res_data[:4]!r} (expected b'RSCE')")

    off, bmp_size, w, signed_h, bf_off_bits = find_bmp_slot(bytes(res_data))
    orient = "top-down" if signed_h < 0 else "bottom-up"
    print(f"Detected BMP slot at offset {off}: {w}x{abs(signed_h)} 24bpp {orient} ({bmp_size} bytes)")

    bmp_bytes = build_matching_bmp(args.image, w, signed_h, bmp_size, bf_off_bits)
    res_data[off : off + bmp_size] = bmp_bytes

    out_dir = os.path.dirname(os.path.abspath(args.resource_out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.resource_out, "wb") as f:
        f.write(res_data)
    print(f"OK: wrote patched resource image {args.resource_out} ({len(res_data)} bytes)")


if __name__ == "__main__":
    main()
