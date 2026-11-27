"""Sync ROMs to external storage, generate metadata.pegasus.txt, and scrape
boxfront/screenshot artwork from github.com/libretro-thumbnails.

Artwork lookup first downloads each repo's file list once (GitHub tree API) and
matches titles locally (exact, then region/tag-insensitive), so only images that
really exist are requested. Falls back to blind URL guessing when the index is
unavailable (rate limit, truncated tree, offline). Set GITHUB_TOKEN to raise the
API rate limit.
"""
import argparse
import concurrent.futures
import html as html_mod
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request

GH_THUMB_BASE = "https://raw.githubusercontent.com/libretro-thumbnails"
GH_TREE_API = "https://api.github.com/repos/libretro-thumbnails/{repo}/git/trees/master?recursive=1"
UA = {"User-Agent": "tvbox-arcade-builder"}
THUMB_KINDS = ("Named_Boxarts", "Named_Snaps")
REGION_PREFERENCE = ("(USA", "(World", "(Europe", "(Japan")
RETRY_STATUS = {403, 429, 500, 502, 503, 504}

BIOS_ZIPS = {
    "neogeo.zip", "pgm.zip", "qsound.zip", "qsound_hle.zip", "skns.zip",
    "isgsm.zip", "bubsys.zip", "namcoc69.zip", "namcoc70.zip", "namcoc75.zip", "ym2608.zip",
}

ES_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")


def copy_if_needed(src: str, dst: str) -> bool:
    if os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src):
        return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    return True


def load_xml_dat(dat_path: str) -> dict[str, tuple[str, str, str]]:
    """Parse ClrMamePro / Logiqx / modern MAME XML DAT (<game> or <machine>) tolerantly."""
    if not dat_path or not os.path.exists(dat_path):
        return {}
    with open(dat_path, "r", encoding="utf-8", errors="ignore") as f:
        raw = f.read()
    info: dict[str, tuple[str, str, str]] = {}
    pattern = re.compile(r'<(game|machine)\s+name="([^"]+)"[^>]*>(.*?)</\1>', re.S)
    for m in pattern.finditer(raw):
        name = html_mod.unescape(m.group(2)).strip()
        body = m.group(3)
        dm = re.search(r"<description>(.*?)</description>", body, re.S)
        ym = re.search(r"<year>(.*?)</year>", body, re.S)
        mm = re.search(r"<manufacturer>(.*?)</manufacturer>", body, re.S)
        desc = html_mod.unescape(dm.group(1)).strip() if dm else name
        year = html_mod.unescape(ym.group(1)).strip() if ym else ""
        manu = html_mod.unescape(mm.group(1)).strip() if mm else ""
        if name:
            info[name] = (desc, year, manu)
    return info


def sanitize_libretro_name(title: str) -> str:
    return re.sub(r'[&\\/:*?"<>|]', "_", title).strip()


def one_line(text: str) -> str:
    """metadata.pegasus.txt values must stay on a single line."""
    return " ".join(text.split())


def http_get(url: str, headers: dict | None = None, timeout: int = 15, retries: int = 3) -> bytes | None:
    """GET with exponential backoff on rate limits / transient errors. Returns None on 404 or give-up."""
    hdrs = dict(UA)
    hdrs.update(headers or {})
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRY_STATUS:
                return None
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        if attempt < retries - 1:
            time.sleep(1.5 * (2 ** attempt))
    return None


def try_download(urls: list[str], dest: str) -> bool:
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return True
    for url in urls:
        data = http_get(url)
        if data and data[:4] == b"\x89PNG":
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(data)
            return True
    return False


def normalize_title(title: str) -> str:
    """Region/tag-insensitive key: 'Metal Slug (World) [!]' -> 'metalslug'."""
    no_tags = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", title)
    return re.sub(r"[^0-9a-z]+", "", no_tags.lower())


def _region_rank(name: str) -> int:
    for i, tag in enumerate(REGION_PREFERENCE):
        if tag in name:
            return i
    return len(REGION_PREFERENCE)


class ThumbIndex:
    """Available thumbnail names per kind (Named_Boxarts / Named_Snaps) for one repo."""

    def __init__(self, names: dict[str, set[str]]):
        self.names = names
        self.fuzzy: dict[str, dict[str, str]] = {}
        for kind, items in names.items():
            best: dict[str, str] = {}
            for n in sorted(items, key=lambda x: (_region_rank(x), x)):
                best.setdefault(normalize_title(n), n)
            self.fuzzy[kind] = best

    @classmethod
    def from_tree(cls, tree: list[dict]) -> "ThumbIndex":
        names: dict[str, set[str]] = {k: set() for k in THUMB_KINDS}
        for item in tree:
            kind, _, fn = item.get("path", "").partition("/")
            if kind in names and fn.lower().endswith(".png") and "/" not in fn:
                names[kind].add(fn[:-4])
        return cls(names)

    def resolve(self, kind: str, candidates: list[str]) -> str | None:
        available = self.names.get(kind, set())
        for c in candidates:
            if c in available:
                return c
        fuzzy = self.fuzzy.get(kind, {})
        for c in candidates:
            hit = fuzzy.get(normalize_title(c))
            if hit:
                return hit
        return None


def fetch_thumb_index(repo: str) -> ThumbIndex | None:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = http_get(GH_TREE_API.format(repo=urllib.parse.quote(repo)), headers=headers, timeout=60)
    if not data:
        print(f"[index] {repo}: tree API unavailable, falling back to URL guessing")
        return None
    try:
        payload = json.loads(data)
    except ValueError:
        return None
    if payload.get("truncated"):
        print(f"[index] {repo}: tree truncated by GitHub, falling back to URL guessing")
        return None
    return ThumbIndex.from_tree(payload.get("tree", []))


def thumb_url(repo: str, kind: str, title: str) -> str:
    return f"{GH_THUMB_BASE}/{repo}/master/{kind}/{urllib.parse.quote(title + '.png')}"


def plan_thumb_urls(
    kind: str, titles: list[str], repos: list[str], indexes: dict[str, "ThumbIndex | None"]
) -> list[str]:
    """Indexed repos contribute only names that exist; unindexed repos fall back to guessing."""
    urls: list[str] = []
    for repo in repos:
        idx = indexes.get(repo)
        if idx is None:
            urls.extend(thumb_url(repo, kind, t) for t in titles)
        else:
            hit = idx.resolve(kind, titles)
            if hit:
                urls.append(thumb_url(repo, kind, hit))
    return urls


def collect_roms(dest_dir: str, exts: set[str]) -> tuple[list[str], list[str]]:
    """Return (playable_roms, present_bios_files) relative to dest_dir."""
    roms: list[str] = []
    bios_found: list[str] = []
    for root, _, files in os.walk(dest_dir):
        rel_root = os.path.relpath(root, dest_dir)
        if rel_root == "media" or rel_root.startswith("media" + os.sep):
            continue
        if rel_root == "images" or rel_root.startswith("images" + os.sep):
            continue
        for fn in files:
            if os.path.splitext(fn)[1].lower() in exts:
                rel_path = fn if rel_root == "." else os.path.join(rel_root, fn).replace("\\", "/")
                if fn.lower() in BIOS_ZIPS:
                    bios_found.append(rel_path)
                else:
                    roms.append(rel_path)
    return sorted(roms), sorted(bios_found)


def find_es_assets(dest_dir: str, stem: str) -> tuple[str | None, str | None]:
    """Detect existing EmulationStation/Batocera images/<stem>-thumb.* and -image.* files."""
    images_dir = os.path.join(dest_dir, "images")
    if not os.path.isdir(images_dir):
        return None, None
    boxfront = None
    screenshot = None
    for ext in ES_IMAGE_EXTS:
        for suffix in ("-thumb", "-boxfront", "-cover"):
            cand = os.path.join(images_dir, stem + suffix + ext)
            if boxfront is None and os.path.isfile(cand):
                boxfront = f"images/{stem}{suffix}{ext}"
        for suffix in ("-image", "-screenshot", "-snap"):
            cand = os.path.join(images_dir, stem + suffix + ext)
            if screenshot is None and os.path.isfile(cand):
                screenshot = f"images/{stem}{suffix}{ext}"
    return boxfront, screenshot


def write_metadata(
    dest_dir: str,
    collection_name: str,
    shortname: str,
    extensions: str,
    core_name: str,
    retroarch_pkg: str,
    roms: list[str],
    bios_found: list[str],
    dat_info: dict[str, tuple[str, str, str]],
    es_images: bool = False,
):
    ignore_lines = "".join(f"ignore-file: {b}\n" for b in bios_found)
    header = f"""collection: {one_line(collection_name)}
shortname: {shortname}
extensions: {extensions}
{ignore_lines}launch: am start
  -n {retroarch_pkg}/com.retroarch.browser.retroactivity.RetroActivityFuture
  -e ROM {{file.path}}
  -e LIBRETRO /data/data/{retroarch_pkg}/cores/{core_name}
  -e CONFIGFILE /storage/emulated/0/Android/data/{retroarch_pkg}/files/retroarch.cfg
  -e IME com.android.inputmethod.latin/.LatinIME
  -e DATADIR /data/data/{retroarch_pkg}
  -e SDCARD /storage/emulated/0
  -e EXTERNAL /storage/emulated/0/Android/data/{retroarch_pkg}/files
  --activity-clear-task
  --activity-clear-top
  --activity-no-history
"""
    blocks = [header]
    if dat_info or es_images:
        for rel_path in roms:
            stem = os.path.splitext(os.path.basename(rel_path))[0]
            title, year, manu = (one_line(v) for v in dat_info.get(stem, (stem, "", "")))
            b = [f"\ngame: {title or stem}", f"file: {rel_path}"]
            if year.isdigit():
                b.append(f"release: {year}")
            if manu:
                b.append(f"developer: {manu}")
            if es_images:
                bf, ss = find_es_assets(dest_dir, stem)
                if bf:
                    b.append(f"assets.boxFront: {bf}")
                if ss:
                    b.append(f"assets.screenshot: {ss}")
            blocks.append("\n".join(b))

    meta_path = os.path.join(dest_dir, "metadata.pegasus.txt")
    with open(meta_path, "w", encoding="utf-8") as f:
        f.write("\n".join(blocks) + "\n")
    print(f"[{shortname}] Wrote {meta_path} ({len(roms)} ROMs, {len(bios_found)} ignored BIOS)")


def candidate_titles(stem: str, dat_info: dict[str, tuple[str, str, str]]) -> list[str]:
    if stem in dat_info:
        raw_title = dat_info[stem][0]
        return [sanitize_libretro_name(raw_title)]
    clean = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", stem).strip()
    variants = [stem]
    for region in ("USA", "World", "Europe", "Japan"):
        variants.append(f"{clean} ({region})")
    if clean != stem:
        variants.append(clean)
    return list(dict.fromkeys(sanitize_libretro_name(v) for v in variants if v))


def main():
    parser = argparse.ArgumentParser(description="Copy ROMs, generate Pegasus metadata, and fetch artwork.")
    parser.add_argument("--src-dir", default="", help="Optional source ROM folder to copy into --dest-dir")
    parser.add_argument("--dest-dir", required=True, help="Target system ROM folder (e.g., E:\\ROMs\\arcade)")
    parser.add_argument("--shortname", required=True, help="Pegasus collection shortname (e.g., arcade, snes)")
    parser.add_argument("--collection-name", required=True, help="Display name (e.g., Arcade (FBNeo))")
    parser.add_argument("--core", required=True, help="Libretro core filename (e.g., fbneo_libretro_android.so)")
    parser.add_argument("--extensions", required=True, help="Comma-separated extensions (e.g., zip, 7z)")
    parser.add_argument("--dat", default="", help="Optional XML DAT path for short-name to title mapping")
    parser.add_argument("--libretro-repo", default="", help="GitHub libretro-thumbnails repo name")
    parser.add_argument("--fallback-repo", default="", help="Optional fallback libretro-thumbnails repo")
    parser.add_argument("--reuse-media-dir", default="", help="Optional existing media/<stem> directory to reuse")
    parser.add_argument(
        "--es-images",
        action="store_true",
        help="Link existing Batocera/ES images/<stem>-thumb.* and -image.* directly in metadata.pegasus.txt",
    )
    parser.add_argument("--retroarch-pkg", default="com.retroarch.ra32", help="RetroArch Android package ID")
    parser.add_argument("--workers", type=int, default=8, help="Concurrent download threads (default: 8)")
    parser.add_argument(
        "--no-index", action="store_true",
        help="Skip the GitHub tree index and guess thumbnail URLs directly (old behaviour)",
    )
    args = parser.parse_args()

    exts = {("." + e.strip().lower().lstrip(".")) for e in args.extensions.split(",") if e.strip()}
    os.makedirs(args.dest_dir, exist_ok=True)

    if args.src_dir and os.path.isdir(args.src_dir):
        copied = 0
        for root, _, files in os.walk(args.src_dir):
            rel = os.path.relpath(root, args.src_dir)
            if rel == "media" or rel.startswith("media" + os.sep):
                continue
            if rel == "images" or rel.startswith("images" + os.sep):
                continue
            target_sub = args.dest_dir if rel == "." else os.path.join(args.dest_dir, rel)
            for fn in files:
                if os.path.splitext(fn)[1].lower() in exts:
                    if copy_if_needed(os.path.join(root, fn), os.path.join(target_sub, fn)):
                        copied += 1
        print(f"[{args.shortname}] Copied/updated {copied} files from {args.src_dir}")

    dat_info = load_xml_dat(args.dat)
    roms, bios_found = collect_roms(args.dest_dir, exts)
    write_metadata(
        args.dest_dir,
        args.collection_name,
        args.shortname,
        args.extensions,
        args.core,
        args.retroarch_pkg,
        roms,
        bios_found,
        dat_info,
        es_images=args.es_images,
    )

    if not args.libretro_repo or not roms:
        return

    reused = 0
    missing = 0
    tasks: list[tuple[list[str], str]] = []
    repos = [r for r in (args.libretro_repo, args.fallback_repo) if r]
    indexes = {r: (None if args.no_index else fetch_thumb_index(r)) for r in repos}

    for rel_path in roms:
        stem = os.path.splitext(os.path.basename(rel_path))[0]
        mdir = os.path.join(args.dest_dir, "media", stem)
        b_dst = os.path.join(mdir, "boxfront.png")
        s_dst = os.path.join(mdir, "screenshot.png")

        if args.reuse_media_dir:
            for fn, dst in (("boxfront.png", b_dst), ("screenshot.png", s_dst)):
                cand = os.path.join(args.reuse_media_dir, stem, fn)
                if not os.path.exists(dst) and os.path.exists(cand):
                    if copy_if_needed(cand, dst):
                        reused += 1

        titles = candidate_titles(stem, dat_info)
        for kind, dst in (("Named_Boxarts", b_dst), ("Named_Snaps", s_dst)):
            if os.path.exists(dst):
                continue
            urls = plan_thumb_urls(kind, titles, repos, indexes)
            if urls:
                tasks.append((urls, dst))
            else:
                missing += 1

    ok = 0
    if tasks:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
            for res in ex.map(lambda t: try_download(t[0], t[1]), tasks):
                if res:
                    ok += 1
    print(f"[{args.shortname}] Reused: {reused} | Downloaded: {ok}/{len(tasks)} | Not in index: {missing}")


if __name__ == "__main__":
    main()
