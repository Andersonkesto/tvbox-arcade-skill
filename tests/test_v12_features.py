"""Tests for v1.2 features: QML lint, batched audit parsing, RSCE table patching,
thumbnail index matching, deploy/rollback planning and verification parsers."""
import hashlib
import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "tvbox-arcade-builder" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import audit_tvbox
import deploy_tvbox
import lint_qml_theme
import patch_boot_resource
import rollback_tvbox
import sync_usb_media
import verify_tvbox
from tvbox_adb import Adb, AdbError


class TestQmlLint(unittest.TestCase):
    def test_detects_es2020_operators(self):
        src = "var a = b ?? c;\nvar d = e?.f;\nx ??= 1;\ny ||= 2;\n"
        ops = [f["operator"] for f in lint_qml_theme.lint_source(src)]
        self.assertEqual(ops, ["??", "?.", "??=", "||="])

    def test_ignores_strings_comments_and_ternary_decimals(self):
        src = (
            'text: "what?? really?."\n'
            "// legacy: a ?? b\n"
            "/* a?.b */\n"
            "opacity: hovered ?.5 : 1\n"
            "var t = `x ?? y`;\n"
        )
        self.assertEqual(lint_qml_theme.lint_source(src), [])

    def test_line_numbers_after_block_comment(self):
        src = "/* one\n two */\nvar z = a ?? b;\n"
        (f,) = lint_qml_theme.lint_source(src)
        self.assertEqual((f["line"], f["col"]), (3, 11))

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as td:
            good = Path(td) / "good"
            bad = Path(td) / "bad"
            good.mkdir()
            bad.mkdir()
            (good / "Main.qml").write_text("Item { property var x: a ? b : c }\n", encoding="utf-8")
            (bad / "Main.qml").write_text("Item { property var x: a ?? c }\n", encoding="utf-8")
            run = [sys.executable, str(SCRIPTS_DIR / "lint_qml_theme.py")]
            self.assertEqual(subprocess.run(run + [str(good)], capture_output=True).returncode, 0)
            self.assertEqual(subprocess.run(run + [str(bad)], capture_output=True).returncode, 1)


class TestAuditParsing(unittest.TestCase):
    def test_batch_roundtrip(self):
        script = audit_tvbox.build_batch_script({"a": "echo 1", "b": "echo 2"})
        self.assertIn(audit_tvbox.MARKER, script)
        out = f"{audit_tvbox.MARKER} a\r\nline1\r\n{audit_tvbox.MARKER} b\r\n\r\n"
        self.assertEqual(audit_tvbox.parse_batch_output(out), {"a": "line1", "b": ""})

    def test_df_toolbox_and_toybox(self):
        toolbox = "Filesystem  Size   Used   Free   Blksize\n/system  1.2G  1.1G  12.0M  4096\n/data  4.0G  1.0G  3.0G  4096\n"
        self.assertEqual(audit_tvbox.parse_df_free_mb(toolbox), {"/system": 12.0, "/data": 3072.0})
        toybox = ("Filesystem 1K-blocks Used Available Use% Mounted on\n"
                  "/dev/block/rknand_system 1048576 1038336 10240 99% /system\n")
        self.assertEqual(audit_tvbox.parse_df_free_mb(toybox), {"/system": 10.0})

    def test_report_new_warnings(self):
        raw = {
            "sdk": "25", "abi": "armeabi-v7a",
            "df": "/system  1.2G  1.1G  12.0M  4096\n/data  4.0G  1.0G  3.0G  4096",
            "thermal": "81000\n45000", "storage": "emulated self 1234-ABCD",
            "root": "", "packages": "package:com.retroarch.ra32\npackage:com.android.shell",
            "wm_size": "Physical size: 1280x720",
        }
        r = audit_tvbox.build_report(raw)
        self.assertEqual(r["external_volumes"], ["1234-ABCD"])
        self.assertEqual(r["display_size"], "1280x720")
        self.assertEqual(r["installed"], {"retroarch": True, "pegasus": False})
        self.assertEqual(r["soc_temps_c"], [81.0, 45.0])
        joined = " ".join(r["warnings"])
        for needle in ("/system has only 12.0 MB", "/data has 3072.0 MB", "81.0 C", "No root"):
            self.assertIn(needle, joined)


def _bmp(mode: str, size=(120, 100), color=(10, 20, 30)) -> bytes:
    im = Image.new("RGB", size, color)
    if mode == "P":
        # Real 8-bit logos carry a full palette; a gradient guarantees 256 entries.
        grad = Image.linear_gradient("L").resize(size)
        im = Image.merge("RGB", (grad, grad.rotate(90), grad.transpose(Image.Transpose.FLIP_LEFT_RIGHT)))
        im = im.convert("P", palette=Image.Palette.ADAPTIVE, colors=256)
    buf = io.BytesIO()
    im.save(buf, format="BMP")
    return buf.getvalue()


def _rsce(entries: list[tuple[str, bytes, bool]]) -> bytes:
    """Build a synthetic RSCE image. entries = [(name, content, new_layout_with_sha256)]."""
    n = len(entries)
    header = b"RSCE" + struct.pack("<HH", 0, 0) + bytes([1, 1, 1]) + struct.pack("<I", n)
    blob = bytearray(header.ljust(512, b"\x00"))
    table = bytearray()
    data = bytearray()
    blk = 1 + n
    for name, content, new_layout in entries:
        nm = name.encode().ljust(256, b"\x00")
        if new_layout:
            digest = hashlib.sha256(content).digest()
            entry = b"ENTR" + nm + digest + struct.pack("<III", 32, blk, len(content))
        else:
            entry = b"ENTR" + nm + struct.pack("<II", blk, len(content))
        table += entry.ljust(512, b"\x00")
        padded = content.ljust(((len(content) + 511) // 512) * 512, b"\x00")
        data += padded
        blk += len(padded) // 512
    return bytes(blob + table + data)


class TestRsceLogoPatch(unittest.TestCase):
    def test_patches_both_logos_24_and_8_bit_and_refreshes_hash(self):
        logo24 = _bmp("RGB", (134, 100))
        logo8 = _bmp("P", (120, 100))
        img = _rsce([("rk-kernel.dtb", b"\xd0\x0d\xfe\xed" * 64, False),
                     ("logo.bmp", logo24, False),
                     ("logo_kernel.bmp", logo8, True)])

        entries = patch_boot_resource.parse_rsce_entries(img)
        self.assertEqual([e["name"] for e in entries], ["rk-kernel.dtb", "logo.bmp", "logo_kernel.bmp"])
        slots = patch_boot_resource.find_logo_slots(img)
        self.assertEqual([(s["name"], s["bpp"]) for s in slots], [("logo.bmp", 24), ("logo_kernel.bmp", 8)])

        with tempfile.TemporaryDirectory() as td:
            res_in, res_out = Path(td) / "res.img", Path(td) / "out.img"
            new_img, extract = Path(td) / "new.png", Path(td) / "logos"
            res_in.write_bytes(img)
            Image.new("RGB", (400, 100), (255, 184, 0)).save(new_img)
            proc = subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "patch_boot_resource.py"), "--resource-in", str(res_in),
                 "--image", str(new_img), "--resource-out", str(res_out), "--extract", str(extract)],
                capture_output=True, text=True, check=True,
            )
            self.assertIn("refreshed sha256", proc.stdout)
            self.assertTrue((extract / "logo.png").exists() and (extract / "logo_kernel.png").exists())
            out = res_out.read_bytes()

        self.assertEqual(len(out), len(img))
        for s in patch_boot_resource.find_logo_slots(out):
            content = out[s["offset"]: s["offset"] + s["size"]]
            with Image.open(io.BytesIO(content)) as im:
                self.assertEqual(im.size, (s["width"], abs(s["height"])))
                center = im.convert("RGB").getpixel((im.width // 2, im.height // 2))
                self.assertGreater(center[0], 200)  # new orange logo, not the old dark one
            entry = s["entry"]
            if entry["hash_size"] == 32:
                stored = out[entry["hash_pos"]: entry["hash_pos"] + 32]
                self.assertEqual(stored, hashlib.sha256(content).digest())

    def test_list_only_writes_nothing(self):
        img = _rsce([("logo.bmp", _bmp("RGB"), False)])
        with tempfile.TemporaryDirectory() as td:
            res_in = Path(td) / "res.img"
            res_in.write_bytes(img)
            proc = subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "patch_boot_resource.py"), "--resource-in", str(res_in), "--list"],
                capture_output=True, text=True, check=True,
            )
            self.assertIn("ENTR logo.bmp", proc.stdout)
            self.assertEqual(sorted(os.listdir(td)), ["res.img"])


class TestThumbIndex(unittest.TestCase):
    def setUp(self):
        tree = [
            {"path": "Named_Boxarts/Metal Slug - Super Vehicle-001 (World).png"},
            {"path": "Named_Boxarts/Super Mario World (USA).png"},
            {"path": "Named_Boxarts/Super Mario World (Japan).png"},
            {"path": "Named_Snaps/Super Mario World (Europe).png"},
            {"path": "Named_Titles/ignored.png"},
        ]
        self.idx = sync_usb_media.ThumbIndex.from_tree(tree)

    def test_exact_then_region_insensitive_with_preference(self):
        self.assertEqual(self.idx.resolve("Named_Boxarts", ["Super Mario World (USA)"]), "Super Mario World (USA)")
        self.assertEqual(self.idx.resolve("Named_Boxarts", ["Super Mario World"]), "Super Mario World (USA)")
        self.assertEqual(self.idx.resolve("Named_Snaps", ["Super Mario World [!]"]), "Super Mario World (Europe)")
        self.assertIsNone(self.idx.resolve("Named_Boxarts", ["Unknown Game"]))

    def test_plan_urls_index_and_fallback(self):
        urls = sync_usb_media.plan_thumb_urls(
            "Named_Boxarts", ["Super Mario World"], ["Nintendo - SNES", "Fallback"],
            {"Nintendo - SNES": self.idx, "Fallback": None},
        )
        self.assertEqual(len(urls), 2)
        self.assertIn("Super%20Mario%20World%20%28USA%29.png", urls[0])
        self.assertIn("/Fallback/master/Named_Boxarts/Super%20Mario%20World.png", urls[1])

    def test_metadata_values_are_single_line(self):
        with tempfile.TemporaryDirectory() as td:
            sync_usb_media.write_metadata(
                td, "Arcade", "arcade", "zip", "fbneo_libretro_android.so", "com.retroarch.ra32",
                ["mslug.zip"], [], {"mslug": ("Metal Slug\n  (set 1)", "1996", "SNK\n")},
            )
            meta = Path(td, "metadata.pegasus.txt").read_text(encoding="utf-8")
        self.assertIn("game: Metal Slug (set 1)\n", meta)
        self.assertIn("developer: SNK\n", meta)


class FakeAdb(Adb):
    def __init__(self, responses: dict[str, str], apply=False, use_su=False):
        super().__init__("adb", "", apply=apply, use_su=use_su)
        self.responses = responses
        self.calls: list[str] = []

    def query(self, cmd, privileged=False, timeout=30):
        self.calls.append("query " + cmd)
        for key, val in self.responses.items():
            if key in cmd:
                return val
        return ""

    def pull(self, remote, local):
        return False

    def _mutate(self, argv, label, timeout=600):
        self.calls.append(label)
        return True


class TestDeployAndRollback(unittest.TestCase):
    def test_protected_packages(self):
        for pkg in ("com.android.systemui", "com.android.settings", "com.retroarch.ra32", "com.android.shell"):
            self.assertTrue(deploy_tvbox.is_protected(pkg), pkg)
        self.assertFalse(deploy_tvbox.is_protected("com.droidlogic.mediacenter"))

    def test_dry_run_changes_nothing_and_saves_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            rb_path = os.path.join(td, "rb.json")
            adb = FakeAdb({"resolve-activity": "priority=0\ncom.android.launcher3/.Launcher"})
            d = deploy_tvbox.Deployer(adb, deploy_tvbox.Rollback(rb_path, ""), os.path.join(td, "bk"))
            d.set_home()
            self.assertIn("adb shell cmd package set-home-activity org.pegasus_frontend.android/.MainActivity", adb.calls)
            self.assertFalse(os.path.exists(rb_path))

    def test_apply_records_first_originals_then_rollback_restores(self):
        with tempfile.TemporaryDirectory() as td:
            rb_path = os.path.join(td, "rb.json")
            responses = {
                "resolve-activity": "com.android.launcher3/.Launcher",
                "settings get": "1.0",
                "pm list packages": "package:com.droidlogic.mediacenter\npackage:com.android.systemui",
            }
            adb = FakeAdb(responses, apply=True)
            d = deploy_tvbox.Deployer(adb, deploy_tvbox.Rollback(rb_path, "1.2.3.4:5555"), os.path.join(td, "bk"))
            d.set_home()
            d.android_kiosk()
            d.debloat(["com.droidlogic.mediacenter", "com.android.systemui", "not.installed"])
            self.assertNotIn("adb shell pm disable-user --user 0 com.android.systemui", adb.calls)

            # A second run (Pegasus already HOME, settings already 0) must keep the first originals.
            adb2 = FakeAdb({"resolve-activity": "org.pegasus_frontend.android/.MainActivity", "settings get": "0"},
                           apply=True)
            d2 = deploy_tvbox.Deployer(adb2, deploy_tvbox.Rollback(rb_path, ""), os.path.join(td, "bk"))
            d2.set_home()
            d2.android_kiosk()

            data = json.loads(Path(rb_path).read_text(encoding="utf-8"))
            self.assertEqual(data["home"], {"original": "com.android.launcher3/.Launcher"})
            self.assertEqual(data["disabled_packages"], ["com.droidlogic.mediacenter"])
            self.assertTrue(all(s["previous"] == "1.0" for s in data["settings"]))

            rb_adb = FakeAdb({}, apply=True)
            self.assertEqual(rollback_tvbox.rollback(rb_adb, data), [])
            self.assertIn("adb shell cmd package set-home-activity com.android.launcher3/.Launcher", rb_adb.calls)
            self.assertIn("adb shell pm enable com.droidlogic.mediacenter", rb_adb.calls)
            self.assertIn("adb shell settings put global window_animation_scale 1.0", rb_adb.calls)

    def test_root_steps_require_su(self):
        with tempfile.TemporaryDirectory() as td:
            d = deploy_tvbox.Deployer(FakeAdb({}), deploy_tvbox.Rollback(os.path.join(td, "rb.json"), ""), td)
            with self.assertRaises(AdbError):
                d.keylayouts()
            Path(td, "x_libretro_android.so").write_bytes(b"\x7fELF")
            with self.assertRaises(AdbError):
                d.cores(td)

    def test_theme_lint_and_main_suffix_guard(self):
        with tempfile.TemporaryDirectory() as td:
            d = deploy_tvbox.Deployer(FakeAdb({}), deploy_tvbox.Rollback(os.path.join(td, "rb.json"), ""), td)
            bad = Path(td, "Bad")
            bad.mkdir()
            (bad / "theme.qml").write_text("Item { x: a ?? b }", encoding="utf-8")
            with self.assertRaises(AdbError):
                d.theme(str(bad), force=False)
            main = Path(td, "RGLegacy-main")
            main.mkdir()
            (main / "theme.qml").write_text("Item {}", encoding="utf-8")
            with self.assertRaises(AdbError):
                d.theme(str(main), force=False)


class TestVerifyParsers(unittest.TestCase):
    def test_lastrun(self):
        ok = "Theme: RGLegacy\n42 games found\n"
        self.assertEqual(verify_tvbox.check_lastrun(ok, "RGLegacy")["status"], "PASS")
        bad = "Theme: RGLegacy\nqrc:/Main.qml:10: Syntax error\n"
        self.assertEqual(verify_tvbox.check_lastrun(bad, "RGLegacy")["status"], "FAIL")
        self.assertEqual(verify_tvbox.check_lastrun("", "")["status"], "FAIL")

    def test_focus_home_bgm(self):
        self.assertEqual(verify_tvbox.check_focus(
            "  mCurrentFocus=Window{1 u0 org.pegasus_frontend.android/.MainActivity}")["status"], "PASS")
        self.assertEqual(verify_tvbox.check_home("com.android.launcher3/.Launcher")["status"], "FAIL")
        af = "Standby: no\n  1 Tracks of which 1 are active\n"
        self.assertEqual(verify_tvbox.check_bgm(af)["status"], "PASS")
        self.assertEqual(verify_tvbox.check_bgm("Standby: yes\n 1 Tracks of which 0 are active")["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
