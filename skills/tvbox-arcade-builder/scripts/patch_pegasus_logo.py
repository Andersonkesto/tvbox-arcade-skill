"""Dynamically replace the embedded loading logo PNG inside Pegasus Frontend's
native Android library (.so) and produce an unsigned APK ready for zipalign + signing.

Note: Always back up /sdcard/Android/data/org.pegasus_frontend.android/ before
uninstalling the official APK, because re-signing uses a different certificate.
"""
import argparse
import io
import os
import shutil
import struct
import subprocess
import zipfile

try:
    from PIL import Image, ImageDraw
except ImportError as exc:
    raise SystemExit("Missing dependency: Pillow. Install it with: pip install pillow") from exc

SIGNATURE_EXTS = (".sf", ".rsa", ".dsa", ".ec", ".sig")


def is_apk_signature_entry(filename: str) -> bool:
    """Return True only for APK v1 signature files inside META-INF/."""
    upper = filename.upper()
    if not upper.startswith("META-INF/"):
        return False
    base = filename[len("META-INF/"):]
    if "/" in base:
        return False
    return base.upper() == "MANIFEST.MF" or base.lower().endswith(SIGNATURE_EXTS)


def find_target_logo_slot(so_data: bytes) -> tuple[int, int, tuple[int, int]]:
    """Locate the splash logo PNG slot inside qt_resource_data (prefers 700x227 or largest banner)."""
    candidates = []
    pos = 0
    while True:
        idx = so_data.find(b"\x89PNG\r\n\x1a\n", pos)
        if idx == -1:
            break
        pos = idx + 8
        if idx < 4:
            continue
        rec_len, = struct.unpack(">I", so_data[idx - 4 : idx])
        if 100 <= rec_len <= 500000 and idx + rec_len <= len(so_data):
            blob = so_data[idx : idx + rec_len]
            if b"IEND" in blob[-16:]:
                try:
                    im = Image.open(io.BytesIO(blob))
                    if im.size == (700, 227):
                        return idx, rec_len, im.size
                    if im.width >= 300 and rec_len >= 10000:
                        candidates.append((rec_len, idx, im.size))
                except Exception:
                    pass
    if candidates:
        candidates.sort(reverse=True)
        rec_len, idx, size = candidates[0]
        return idx, rec_len, size
    raise RuntimeError("Could not locate embedded splash logo PNG in native library")


def quantize_to_fit(canvas: Image.Image, max_len: int) -> bytes:
    for colors in (256, 128, 96, 64, 48, 32, 24, 16):
        q = canvas.quantize(colors=colors, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
        buf = io.BytesIO()
        q.save(buf, format="PNG", optimize=True)
        b = buf.getvalue()
        if len(b) <= max_len:
            print(f"Quantized to {colors} colors -> {len(b)} bytes (slot limit {max_len})")
            return b
    raise RuntimeError(f"Could not compress PNG under {max_len} bytes; reduce --width/--height")


def main():
    parser = argparse.ArgumentParser(description="Patch Pegasus APK embedded splash logo PNG.")
    parser.add_argument("--apk", required=True, help="Source official Pegasus Android APK")
    parser.add_argument("--image", required=True, help="Custom logo/banner image (PNG/JPG)")
    parser.add_argument("--out-unsigned", required=True, help="Output unsigned APK path")
    parser.add_argument("--width", type=int, default=560, help="Target rendered PNG width (default: 560)")
    parser.add_argument("--height", type=int, default=182, help="Target rendered PNG height (default: 182)")
    parser.add_argument("--radius", type=int, default=20, help="Rounded corner radius in px (default: 20)")
    parser.add_argument(
        "--so-entry",
        default="lib/armeabi-v7a/libpegasus-fe_armeabi-v7a.so",
        help="APK path to native library",
    )
    parser.add_argument("--keystore", default="", help="Optional keystore: also run zipalign -p 4 + apksigner")
    parser.add_argument(
        "--ks-pass", default="",
        help="Passed to apksigner --ks-pass (e.g. env:KS_PASS or file:pass.txt). Omit to be prompted.",
    )
    parser.add_argument("--ks-alias", default="", help="Optional key alias inside the keystore")
    parser.add_argument("--out-signed", default="", help="Signed APK path (default: <out-unsigned>-signed.apk)")
    parser.add_argument("--zipalign", default="zipalign", help="Path to zipalign (Android build-tools)")
    parser.add_argument("--apksigner", default="apksigner", help="Path to apksigner (Android build-tools)")
    args = parser.parse_args()

    im = Image.open(args.image).convert("RGBA")
    scale = min(args.width / im.width, args.height / im.height)
    nw, nh = max(1, int(im.width * scale)), max(1, int(im.height * scale))
    resized = im.resize((nw, nh), Image.LANCZOS)

    mask = Image.new("L", (nw, nh), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, nw - 1, nh - 1], radius=args.radius, fill=255)
    rounded = Image.new("RGBA", (nw, nh), (0, 0, 0, 0))
    rounded.paste(resized, (0, 0), mask)

    canvas = Image.new("RGBA", (args.width, args.height), (0, 0, 0, 0))
    canvas.paste(rounded, ((args.width - nw) // 2, (args.height - nh) // 2), rounded)

    with zipfile.ZipFile(args.apk, "r") as zin:
        if args.so_entry not in zin.namelist():
            raise RuntimeError(f"Entry {args.so_entry} not found in {args.apk}")
        so_data = bytearray(zin.read(args.so_entry))
        off, max_len, orig_size = find_target_logo_slot(bytes(so_data))
        print(f"Found logo slot at 0x{off:x} ({orig_size[0]}x{orig_size[1]}, {max_len} bytes)")

        png_bytes = quantize_to_fit(canvas, max_len)
        so_data[off - 4 : off] = struct.pack(">I", len(png_bytes))
        so_data[off : off + len(png_bytes)] = png_bytes
        if len(png_bytes) < max_len:
            so_data[off + len(png_bytes) : off + max_len] = b"\x00" * (max_len - len(png_bytes))

        os.makedirs(os.path.dirname(os.path.abspath(args.out_unsigned)), exist_ok=True)
        with zipfile.ZipFile(args.out_unsigned, "w") as zout:
            for item in zin.infolist():
                if is_apk_signature_entry(item.filename):
                    continue
                payload = bytes(so_data) if item.filename == args.so_entry else zin.read(item.filename)
                zi = zipfile.ZipInfo(item.filename, date_time=item.date_time)
                zi.compress_type = item.compress_type
                zi.external_attr = item.external_attr
                zout.writestr(zi, payload)

    print(f"OK: wrote unsigned APK {args.out_unsigned}")
    if not args.keystore:
        print("Next: align with `zipalign -p 4` and sign with your own keystore via `apksigner` before installing.")
        return
    signed = args.out_signed or os.path.splitext(args.out_unsigned)[0] + "-signed.apk"
    align_and_sign(args, signed)
    print(f"OK: wrote aligned + signed APK {signed}")
    print("Back up Android/data/org.pegasus_frontend.android/ before uninstalling the official APK.")


def resolve_tool(name: str) -> str:
    found = shutil.which(name) or shutil.which(name + ".bat") or shutil.which(name + ".exe")
    if not found:
        raise SystemExit(f"{name} not found: install Android build-tools or pass its full path")
    return found


def align_and_sign(args, signed_path: str) -> None:
    aligned = os.path.splitext(args.out_unsigned)[0] + "-aligned.apk"
    subprocess.run([resolve_tool(args.zipalign), "-f", "-p", "4", args.out_unsigned, aligned], check=True)
    cmd = [resolve_tool(args.apksigner), "sign", "--ks", args.keystore]
    if args.ks_pass:
        cmd += ["--ks-pass", args.ks_pass]
    if args.ks_alias:
        cmd += ["--ks-key-alias", args.ks_alias]
    cmd += ["--out", signed_path, aligned]
    subprocess.run(cmd, check=True)
    subprocess.run([resolve_tool(args.apksigner), "verify", signed_path], check=True)
    os.remove(aligned)


if __name__ == "__main__":
    main()
