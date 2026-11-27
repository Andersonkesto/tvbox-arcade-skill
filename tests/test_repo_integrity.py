"""Static checks on the skill package: manifests, SKILL.md frontmatter, links and config syntax."""
import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / "skills" / "tvbox-arcade-builder"
RES = SKILL_DIR / "resources"

CFG_LINE = re.compile(r'^[a-z0-9_\-]+ = "[^"]*"$')
KL_LINE = re.compile(r"^(key \d+\s+[A-Z0-9_]+|axis 0x[0-9a-fA-F]+\s+[A-Z_]+)$")
PEGASUS_LINE = re.compile(r"^[a-z0-9_.\-]+: \S.*$")


def content_lines(path: Path):
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        s = line.strip()
        if s and not s.startswith("#"):
            yield n, s


class TestManifests(unittest.TestCase):
    def test_versions_match(self):
        plugin = json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        market = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        entry = next(p for p in market["plugins"] if p["name"] == plugin["name"])
        self.assertEqual(entry["version"], plugin["version"])

    def test_skill_frontmatter(self):
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        self.assertIsNotNone(m, "SKILL.md must start with YAML frontmatter")
        fm = m.group(1)
        self.assertRegex(fm, r"(?m)^name: tvbox-arcade-builder$")
        self.assertRegex(fm, r"(?m)^description:")


class TestLinks(unittest.TestCase):
    def test_relative_markdown_links_exist(self):
        md_files = [REPO_ROOT / "README.md", *SKILL_DIR.rglob("*.md")]
        broken = []
        for md in md_files:
            for target in re.findall(r"\]\(([^)\s]+)\)", md.read_text(encoding="utf-8")):
                if re.match(r"^(https?:|mailto:|#)", target):
                    continue
                path = (md.parent / target.split("#")[0]).resolve()
                if not path.exists():
                    broken.append(f"{md.relative_to(REPO_ROOT)} -> {target}")
        self.assertEqual(broken, [])

    def test_every_script_is_documented_in_skill_md(self):
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        for script in (SKILL_DIR / "scripts").glob("*.py"):
            if script.name == "tvbox_adb.py":
                continue  # internal helper module
            self.assertIn(f"scripts/{script.name}", skill, script.name)


class TestConfigSyntax(unittest.TestCase):
    def test_retroarch_cfg_files(self):
        files = [RES / "retroarch.cfg", RES / "retroarch-core-options.cfg", *(RES / "autoconfig").glob("*.cfg")]
        for f in files:
            keys = []
            for n, line in content_lines(f):
                self.assertRegex(line, CFG_LINE, f"{f.name}:{n}")
                keys.append(line.split(" = ")[0])
            self.assertEqual(len(keys), len(set(keys)), f"duplicate keys in {f.name}")

    def test_keylayouts(self):
        for f in (RES / "keylayouts").glob("*.kl"):
            for n, line in content_lines(f):
                self.assertRegex(line, KL_LINE, f"{f.name}:{n}")

    def test_pegasus_settings(self):
        for n, line in content_lines(RES / "pegasus_settings.txt"):
            self.assertRegex(line, PEGASUS_LINE, f"pegasus_settings.txt:{n}")

    def test_cabinet_safety_invariants(self):
        ra = (RES / "retroarch.cfg").read_text(encoding="utf-8")
        self.assertIn('config_save_on_exit = "false"', ra)
        zd = dict(line.split(" = ") for _, line in content_lines(RES / "autoconfig" / "DragonRise_ZeroDelay.cfg"))
        # Coin acceptor pulses Select: it must never be the hotkey enable button.
        self.assertNotEqual(zd["input_enable_hotkey_btn"], zd["input_select_btn"])
        game_buttons = {v for k, v in zd.items() if k.endswith("_btn") and k not in (
            "input_enable_hotkey_btn", "input_exit_emulator_btn", "input_menu_toggle_btn",
            "input_save_state_btn", "input_load_state_btn")}
        self.assertNotIn(zd["input_enable_hotkey_btn"], game_buttons)


class TestPortalContent(unittest.TestCase):
    """docs/portal-content.json feeds the phase cards on the project website."""

    REQUIRED = ("id", "slot", "shortTitle", "title", "subtitle", "summary", "bullets", "commandLabel", "command")

    def test_portal_content_schema(self):
        path = REPO_ROOT / "docs" / "portal-content.json"
        if not path.exists():
            self.skipTest("optional file")
        data = json.loads(path.read_text(encoding="utf-8"))
        phases = data.get("phases")
        self.assertIsInstance(phases, list)
        self.assertGreater(len(phases), 0)
        ids = []
        for i, phase in enumerate(phases):
            for key in self.REQUIRED:
                self.assertIn(key, phase, f"phase {i} missing {key}")
            self.assertIsInstance(phase["bullets"], list, f"phase {i} bullets")
            self.assertTrue(all(isinstance(b, str) and b for b in phase["bullets"]), f"phase {i} bullets")
            ids.append(phase["id"])
        self.assertEqual(len(ids), len(set(ids)), "duplicate phase ids")

    def test_portal_content_mentions_existing_scripts(self):
        path = REPO_ROOT / "docs" / "portal-content.json"
        if not path.exists():
            self.skipTest("optional file")
        text = path.read_text(encoding="utf-8")
        for name in set(re.findall(r"scripts/([a-z0-9_]+\.py)", text)):
            self.assertTrue((SKILL_DIR / "scripts" / name).exists(), f"portal-content.json cites missing {name}")


if __name__ == "__main__":
    unittest.main()
