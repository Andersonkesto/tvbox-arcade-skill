"""Unit tests for tvbox-arcade-builder helper scripts using synthetic in-memory fixtures."""
import io
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "tvbox-arcade-builder" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import audit_tvbox
import patch_boot_resource


def make_synthetic_bmp(width: int, height: int, top_down: bool = False, color=(20, 40, 80)) -> bytes:
    im = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    im.save(buf, format="BMP")
    raw = bytearray(buf.getvalue())
    if top_down:
        raw[22:26] = struct.pack("<i", -height)
    return bytes(raw)


class TestBootResourcePatch(unittest.TestCase):
    def test_non_multiple_of_4_width_and_top_down(self):
        with tempfile.TemporaryDirectory() as td:
            # 134px width is not divisible by 4 -> requires BMP row stride padding
            orig_bmp = make_synthetic_bmp(134, 100, top_down=True)
            rsce_blob = b"RSCE" + b"\x00" * 124 + orig_bmp + b"\xff" * 64

            res_in = os.path.join(td, "resource_backup.img")
            res_out = os.path.join(td, "out", "resource_patched.img")
            new_img = os.path.join(td, "custom.png")
            with open(res_in, "wb") as f:
                f.write(rsce_blob)
            Image.new("RGB", (320, 200), (255, 184, 0)).save(new_img, format="PNG")

            off, size, w, signed_h, off_bits = patch_boot_resource.find_bmp_slot(rsce_blob)
            self.assertEqual((off, w, signed_h), (128, 134, -100))
            self.assertEqual(size, len(orig_bmp))
            self.assertEqual(off_bits, 54)

            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "patch_boot_resource.py"),
                    "--resource-in", res_in,
                    "--image", new_img,
                    "--resource-out", res_out,
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("top-down", proc.stdout)
            patched = Path(res_out).read_bytes()
            self.assertEqual(len(patched), len(rsce_blob))
            # Verify signed negative height was preserved
            _, _, pw, ph, _ = patch_boot_resource.find_bmp_slot(patched)
            self.assertEqual((pw, ph), (134, -100))


class TestSyncUsbMedia(unittest.TestCase):
    def test_mame_machine_dat_bios_ignore_and_es_images(self):
        with tempfile.TemporaryDirectory() as td:
            rom_dir = Path(td) / "arcade"
            (rom_dir / "images").mkdir(parents=True)
            (rom_dir / "mslug.zip").write_bytes(b"PK\x03\x04dummy")
            (rom_dir / "neogeo.zip").write_bytes(b"PK\x03\x04bios")
            (rom_dir / "images" / "mslug-thumb.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            (rom_dir / "images" / "mslug-image.png").write_bytes(b"\x89PNG\r\n\x1a\n")

            dat_file = Path(td) / "mame.xml"
            dat_file.write_text(
                '<datafile><machine name="mslug"><description>Metal Slug</description>'
                '<year>1996</year><manufacturer>Nazca</manufacturer></machine></datafile>',
                encoding="utf-8",
            )

            # 1) Without DAT, neogeo.zip must still be ignored via ignore-file
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "sync_usb_media.py"),
                    "--dest-dir", str(rom_dir),
                    "--shortname", "arcade",
                    "--collection-name", "Arcade",
                    "--core", "fbneo_libretro_android.so",
                    "--extensions", "zip",
                ],
                check=True,
                capture_output=True,
            )
            meta_no_dat = (rom_dir / "metadata.pegasus.txt").read_text(encoding="utf-8")
            self.assertIn("ignore-file: neogeo.zip", meta_no_dat)

            # 2) With <machine> DAT + --es-images
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "sync_usb_media.py"),
                    "--dest-dir", str(rom_dir),
                    "--shortname", "arcade",
                    "--collection-name", "Arcade",
                    "--core", "fbneo_libretro_android.so",
                    "--extensions", "zip",
                    "--dat", str(dat_file),
                    "--es-images",
                ],
                check=True,
                capture_output=True,
            )
            meta_full = (rom_dir / "metadata.pegasus.txt").read_text(encoding="utf-8")
            self.assertIn("game: Metal Slug", meta_full)
            self.assertIn("assets.boxFront: images/mslug-thumb.png", meta_full)
            self.assertIn("assets.screenshot: images/mslug-image.png", meta_full)
            self.assertNotIn("\nfile: neogeo.zip", meta_full)


class TestMusicQmlGenerator(unittest.TestCase):
    def test_extensions_without_leading_dot_and_qt515_safety(self):
        with tempfile.TemporaryDirectory() as td:
            mdir = Path(td) / "bgm"
            mdir.mkdir()
            (mdir / "01 theme.ogg").write_bytes(b"OggS")
            (mdir / "02 stage.mp3").write_bytes(b"ID3")
            out_qml = Path(td) / "Music.qml"

            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "gen_music_qml.py"),
                    "--music-dir", str(mdir),
                    "--android-music-uri", "file:///storage/1234-ABCD/music",
                    "--extensions", "ogg,mp3",
                    "--out", str(out_qml),
                ],
                check=True,
                capture_output=True,
            )
            content = out_qml.read_text(encoding="utf-8")
            self.assertIn("01 theme.ogg", content)
            self.assertIn("02 stage.mp3", content)
            self.assertNotIn("??", content)
            self.assertNotIn("?.", content)


class TestPegasusLogoPatch(unittest.TestCase):
    def test_patches_logo_and_preserves_meta_inf_services(self):
        with tempfile.TemporaryDirectory() as td:
            # Build synthetic 700x227 PNG slot inside fake .so
            orig_png_buf = io.BytesIO()
            Image.new("RGBA", (700, 227), (40, 80, 120, 255)).save(orig_png_buf, format="PNG")
            orig_png = orig_png_buf.getvalue()
            # Pad to make slot comfortably larger than quantized replacement
            fake_so = b"\x7fELF" + b"\x00" * 60 + struct.pack(">I", len(orig_png)) + orig_png + b"\x00" * 64

            apk_in = Path(td) / "pegasus.apk"
            apk_out = Path(td) / "pegasus-unsigned.apk"
            logo_in = Path(td) / "logo.png"
            Image.new("RGBA", (560, 182), (255, 184, 0, 255)).save(logo_in, format="PNG")

            with zipfile.ZipFile(apk_in, "w") as z:
                z.writestr("lib/armeabi-v7a/libpegasus-fe_armeabi-v7a.so", fake_so)
                z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
                z.writestr("META-INF/CERT.SF", "Signature-Version: 1.0\n")
                z.writestr("META-INF/CERT.RSA", b"fake-cert")
                z.writestr("META-INF/services/example.Service", "com.example.Impl\n")

            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "patch_pegasus_logo.py"),
                    "--apk", str(apk_in),
                    "--image", str(logo_in),
                    "--out-unsigned", str(apk_out),
                ],
                check=True,
                capture_output=True,
            )

            with zipfile.ZipFile(apk_out, "r") as z:
                names = set(z.namelist())
                self.assertNotIn("META-INF/MANIFEST.MF", names)
                self.assertNotIn("META-INF/CERT.SF", names)
                self.assertNotIn("META-INF/CERT.RSA", names)
                self.assertIn("META-INF/services/example.Service", names)
                patched_so = z.read("lib/armeabi-v7a/libpegasus-fe_armeabi-v7a.so")
                self.assertEqual(len(patched_so), len(fake_so))


class TestConsoleCardsAndAudit(unittest.TestCase):
    def test_build_console_cards(self):
        with tempfile.TemporaryDirectory() as td:
            dev = Path(td) / "dev"
            logo = Path(td) / "logo"
            out = Path(td) / "out"
            dev.mkdir()
            logo.mkdir()
            Image.new("RGBA", (120, 80), (200, 200, 200, 255)).save(dev / "snes.png")
            Image.new("RGBA", (140, 40), (255, 255, 255, 255)).save(logo / "snes.png")

            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "build_console_cards.py"),
                    "--devices-dir", str(dev),
                    "--logos-dir", str(logo),
                    "--out-dir", str(out),
                    "--size", "200",
                    "--platforms", "snes",
                ],
                check=True,
                capture_output=True,
            )
            with Image.open(out / "snes.png") as im:
                self.assertEqual(im.size, (200, 200))

    def test_audit_report_detects_raw_nand_and_zero_delay(self):
        sample_raw = {
            "abi": "armeabi-v7a",
            "sdk": "25",
            "release": "7.1.2",
            "board": "rk322x",
            "hardware": "rk30board",
            "uname": "Linux localhost 3.10.104 armv7l",
            "meminfo": "MemTotal:        1024000 kB\nMemFree:          300000 kB\nMemAvailable:     512000 kB\n",
            "partitions": "  31 0 16384 rknand_resource\n",
            "cmdline": "storagemedia=nand",
            "df": "/system 12M free",
            "home": "com.android.launcher3/.Launcher",
            "cpu_freqs": "600000 816000 1008000 1200000",
            "governor": "interactive",
            "inputs": 'I: Bus=0003 Vendor=0079 Product=0006 Version=0110\nN: Name="DragonRise Inc.   Generic   USB  Joystick  "\n',
        }
        report = audit_tvbox.build_report(sample_raw)
        self.assertTrue(report["is_raw_nand"])
        self.assertEqual(report["storage_type"], "raw_nand")
        self.assertEqual(report["memory_mb"]["MemTotal"], 1000)
        self.assertTrue(any("Raw NAND" in w for w in report["warnings"]))
        self.assertTrue(any("0079:0006" in w for w in report["warnings"]))


if __name__ == "__main__":
    unittest.main()
