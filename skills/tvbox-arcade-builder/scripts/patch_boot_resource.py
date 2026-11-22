"""Dynamically patch the embedded 24-bit BMP boot logo inside a Rockchip RSCE
resource partition image without relying on hardcoded offsets.
"""
import argparse
import os
import struct
from PIL import Image


def find_bmp_slot(data: bytes) -> tuple[int, int, int, int]:
    """Return (offset, bmp_size, width, height) of the embedded 24-bit uncompressed BMP."""
    idx = 0
    while True:
        idx = data.find(b"BM", idx)
        if idx == -1:
            break
        if idx + 38 <= len(data):
            size, = struct.unpack("<I", data[idx + 2 : idx + 6])
            w, h = struct.unpack("<ii", data[idx + 18 : idx + 26])
            bpp, = struct.unpack("<H", data[idx + 28 : idx + 30])
            comp, = struct.unpack("<I", data[idx + 30 : idx + 34])
            expected = abs(w) * abs(h) * 3 + 54
            if (
                100 <= w <= 3840
                and 100 <= abs(h) <= 2160
                and bpp == 24
                and comp == 0
                and size == expected
                and idx + size <= len(data)
            ):
                return idx, size, w, abs(h)
        idx += 1
    raise RuntimeError("No valid uncompressed 24-bit BMP logo found inside resource image")


def main():
    parser = argparse.ArgumentParser(description="Safely patch boot logo inside a Rockchip resource.img dump.")
    parser.add_argument("--resource-in", required=True, help="Backed-up resource.img pulled from device")
    parser.add_argument("--image", required=True, help="Replacement image (PNG/JPG/BMP)")
    parser.add_argument("--resource-out", required=True, help="Output patched resource image path")
    args = parser.parse_args()

    res_data = bytearray(open(args.resource_in, "rb").read())
    if res_data[:4] != b"RSCE":
        raise RuntimeError(f"Invalid Rockchip resource header: {res_data[:4]!r} (expected b'RSCE')")

    off, bmp_size, w, h = find_bmp_slot(bytes(res_data))
    print(f"Detected BMP slot at offset {off}: {w}x{h} 24bpp ({bmp_size} bytes)")

    tmp_bmp = args.resource_out + ".tmp.bmp"
    im = Image.open(args.image).convert("RGB").resize((w, h), Image.LANCZOS)
    im.save(tmp_bmp, format="BMP")
    bmp_bytes = open(tmp_bmp, "rb").read()
    os.remove(tmp_bmp)

    if len(bmp_bytes) != bmp_size:
        raise RuntimeError(f"Generated BMP size {len(bmp_bytes)} != target slot size {bmp_size}")

    res_data[off : off + bmp_size] = bmp_bytes
    with open(args.resource_out, "wb") as f:
        f.write(res_data)
    print(f"OK: wrote patched resource image {args.resource_out} ({len(res_data)} bytes)")


if __name__ == "__main__":
    main()
