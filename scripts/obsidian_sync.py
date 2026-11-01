#!/usr/bin/env python3
"""Obsidian の vault で `publish: true` にしたノートを、Hugo の記事に反映する。

    python scripts/obsidian_sync.py --vault ../Obsidian --site .

- 対象: vault 内のすべての .md のうち、先頭の設定欄に `publish: true` があるもの
- 出力: content/posts/<slug>/index.md と、使っている画像（記事のフォルダに置く）
- 管理: この仕組みで作った記事には `obsidianSource` が付く。付いていない記事（WordPress から
  移した記事など）は、読み書きも削除もしない。
- 公開を止める: `publish: true` を外す（またはノートを消す）と、その記事のフォルダを消す。
- 記事の識別は slug。slug を変えると別の記事として扱い、URL も変わる。
"""
import argparse
import datetime
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys

import yaml

JST = datetime.timezone(datetime.timedelta(hours=9))
SKIP_DIRS = {".git", ".obsidian", ".trash", "node_modules", "Superreview_v3.0"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif"}
MAX_PX = 2000
MAX_REMOVALS = 2
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+){0,5}$")
FM_RE = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.S)
FENCE_RE = re.compile(r"^(?P<f>```|~~~)[^\n]*\n.*?^(?P=f)[ \t]*$", re.S | re.M)
INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
X_POST_RE = re.compile(
    r"!\[[^\]]*\]\((https?://(?:www\.)?(?:x|twitter)\.com/[^)\s]+/status/\d+[^)\s]*)\)"
)
LANGS = {"ja", "en", "fi"}
YAML_ERROR_KEY = "_yaml_error"


class Report:
    def __init__(self):
        self.created, self.updated, self.removed = [], [], []
        self.warnings, self.errors = [], []

    def warn(self, msg):
        self.warnings.append(msg)
        print(f"::warning::{msg}")

    def error(self, msg):
        self.errors.append(msg)
        print(f"::error::{msg}")


def split_front_matter(text):
    m = FM_RE.match(text)
    if not m:
        return {}, text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        # publish: true と書かれているのに読めないときに、エラーとして知らせるために印を残す
        msg = str(e).replace("\n", " ")
        return {YAML_ERROR_KEY: (msg, m.group(1))}, text[m.end():]
    return (fm if isinstance(fm, dict) else {}), text[m.end():]


def is_true(v):
    return v is True or (isinstance(v, str) and v.strip().lower() == "true")


def to_jst(v):
    """YAML の日付・日時・文字列を JST の datetime にする。変換できなければ ValueError。"""
    if isinstance(v, datetime.datetime):
        d = v
    elif isinstance(v, datetime.date):
        d = datetime.datetime(v.year, v.month, v.day)
    elif isinstance(v, str):
        d = datetime.datetime.fromisoformat(v.strip().replace(" ", "T", 1))
    else:
        raise ValueError(repr(v))
    if d.tzinfo is None:
        d = d.replace(tzinfo=JST)
    return d.astimezone(JST)


def iso(d):
    return d.isoformat(timespec="seconds")


def git_lastmod(vault, rel):
    try:
        out = subprocess.run(
            ["git", "-C", vault, "log", "-1", "--format=%aI", "--", rel],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return to_jst(out) if out else None
    except (subprocess.CalledProcessError, ValueError, OSError):
        return None


def q(s):
    return json.dumps(s, ensure_ascii=False)


class Vault:
    def __init__(self, root):
        self.root = root
        self.notes = {}  # rel -> (fm, body)
        self.files = {}  # lower file name -> [rel, ...]
        for dp, dn, fn in os.walk(root):
            dn[:] = sorted(d for d in dn if d not in SKIP_DIRS)
            for f in sorted(fn):
                rel = os.path.relpath(os.path.join(dp, f), root).replace(os.sep, "/")
                if f.lower().endswith(".md"):
                    with open(os.path.join(dp, f), encoding="utf-8", errors="replace") as fh:
                        self.notes[rel] = split_front_matter(fh.read())
                else:
                    self.files.setdefault(f.lower(), []).append(rel)

    def find_file(self, name, note_rel):
        """埋め込み・画像の指定から、vault 内の実ファイルを探す。"""
        name = name.replace("\\", "/").lstrip("/")
        note_dir = os.path.dirname(note_rel)
        for cand in (os.path.normpath(os.path.join(note_dir, name)).replace(os.sep, "/"), name):
            if os.path.isfile(os.path.join(self.root, cand)):
                return cand
        hits = self.files.get(os.path.basename(name).lower(), [])
        hits = sorted(hits, key=lambda r: (not r.lower().startswith("pasted images/"), r))
        return hits[0] if hits else None


class Sync:
    def __init__(self, vault, site, dry_run, allow_many):
        self.vault, self.site = Vault(vault), site
        self.posts_dir = os.path.join(site, "content", "posts")
        self.state_path = os.path.join(site, "scripts", "obsidian_sync_state.json")
        self.dry, self.allow_many = dry_run, allow_many
        self.report = Report()
        try:
            self.state = json.load(open(self.state_path, encoding="utf-8"))
        except (OSError, ValueError):
            self.state = {}
        self.state.setdefault("images", {})
        self.now = datetime.datetime.now(JST).replace(microsecond=0)

    # ---- 既存の記事 ----
    def managed_posts(self):
        out = {}
        if not os.path.isdir(self.posts_dir):
            return out
        for name in sorted(os.listdir(self.posts_dir)):
            idx = os.path.join(self.posts_dir, name, "index.md")
            if os.path.isfile(idx):
                text = open(idx, encoding="utf-8").read()
                m = FM_RE.match(text)
                if m and re.search(r"^obsidianSource:", m.group(1), re.M):
                    dm = re.search(r"^date:\s*(\S.*?)\s*$", m.group(1), re.M)
                    out[name] = dm.group(1) if dm else None
        return out

    # ---- 書き込み ----
    def write(self, path, text):
        if os.path.isfile(path) and open(path, encoding="utf-8").read() == text:
            return False
        if not self.dry:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        return True

    def place_image(self, src_rel, slug, used):
        src = os.path.join(self.vault.root, src_rel)
        base, ext = os.path.splitext(os.path.basename(src_rel))
        safe = re.sub(r"[\s#?%\\/]+", "-", base).strip("-") or "image"
        fname = safe + ext.lower()
        if used.get(fname, src_rel) != src_rel:
            fname = f"{safe}-{hashlib.sha1(src_rel.encode()).hexdigest()[:6]}{ext.lower()}"
        used[fname] = src_rel
        dest = os.path.join(self.posts_dir, slug, fname)
        key = f"{slug}/{fname}"
        data = open(src, "rb").read()
        digest = hashlib.sha256(data).hexdigest()
        if os.path.isfile(dest) and self.state["images"].get(key) == digest:
            return fname
        out = self.process_image(data, ext.lower())
        if not self.dry:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as fh:
                fh.write(out)
        self.state["images"][key] = digest
        return fname

    def process_image(self, data, ext):
        """JPEG は位置情報などの EXIF を消して向きを直し、長辺 MAX_PX に縮める。PNG は大きいときだけ縮める。"""
        if ext not in (".jpg", ".jpeg", ".png"):
            return data
        try:
            from PIL import Image, ImageOps

            im = Image.open(io.BytesIO(data))
            is_jpeg = im.format == "JPEG"
            rotated = im.getexif().get(0x0112, 1) not in (1, None)
            big = max(im.size) > MAX_PX
            if not (is_jpeg or big):
                return data
            if rotated:
                im = ImageOps.exif_transpose(im)
            if big:
                im.thumbnail((MAX_PX, MAX_PX), Image.LANCZOS)
            buf = io.BytesIO()
            if is_jpeg:
                if im.mode not in ("RGB", "L"):
                    im = im.convert("RGB")
                im.save(buf, "JPEG", quality=88, optimize=True)
            else:
                im.save(buf, "PNG", optimize=True)
            return buf.getvalue()
        except Exception as e:  # noqa: BLE001 画像が壊れていても同期は止めない
            self.report.warn(f"画像を加工できませんでした（そのままコピーします）: {e}")
            return data

    # ---- 本文の変換 ----
    def convert_body(self, note_rel, slug, title, body, pub_index):
        used_names = {}
        warn = self.report.warn

        def image_md(name, alt="", width=None):
            rel = self.vault.find_file(name, note_rel)
            if not rel:
                warn(f"{note_rel}: 画像が見つかりません: {name}")
                return ""
            fname = self.place_image(rel, slug, used_names)
            url = fname.replace(" ", "%20")
            if width:
                return f'<img src="{url}" alt="{alt}" width="{width}">'
            return f"![{alt}]({url})"

        def embed(m):
            target, _, opt = m.group(1).partition("|")
            name = re.split(r"[#^]", target)[0].strip()
            if os.path.splitext(name)[1].lower() in IMAGE_EXT:
                opt = opt.strip()
                if opt.isdigit():
                    return image_md(name, "", opt)
                return image_md(name, opt)
            warn(f"{note_rel}: 画像以外の埋め込みは未対応のため省略しました: ![[{m.group(1)}]]")
            return ""

        def wikilink(m):
            target, _, alias = m.group(1).partition("|")
            name = re.split(r"[#^]", target)[0].strip()
            stem = os.path.splitext(os.path.basename(name))[0]
            hit = pub_index.get(name.lower()) or pub_index.get(stem.lower()) if name else None
            text = alias.strip() or (stem or re.sub(r"^[#^]+", "", target))
            if hit:
                return f"[{alias.strip() or hit['title']}]({hit['url']})"
            return text

        def md_image(m):
            alt, raw = m.group(1), m.group(2).strip()
            if raw.startswith("<") and ">" in raw:
                url = raw[1: raw.index(">")]
            else:
                url = re.split(r'\s+"', raw)[0]
            if re.match(r"https?://", url):
                return f"![{alt}]({url})"
            from urllib.parse import unquote

            return image_md(unquote(url), alt)

        def transform(seg):
            seg = re.sub(r"%%.*?%%", "", seg, flags=re.S)
            seg = re.sub(r"(?m)[ \t]+\^[A-Za-z0-9-]+$", "", seg)
            seg = X_POST_RE.sub(lambda m: f"[X のポスト]({m.group(1)})", seg)
            seg = re.sub(r"!\[\[([^\]\n]+?)\]\]", embed, seg)
            seg = re.sub(r"(?<!!)\[\[([^\]\n]+?)\]\]", wikilink, seg)
            seg = re.sub(r"!\[([^\]]*)\]\(([^)\n]+)\)", md_image, seg)
            seg = re.sub(r"(?<!=)==(?!=)([^=\n]+?)(?<!=)==(?!=)", r"<mark>\1</mark>", seg)
            seg = re.sub(r"\n{3,}", "\n\n", seg)
            return harden_breaks(seg)

        out, pos = [], 0
        for fm_ in FENCE_RE.finditer(body):
            out.append(self.text_segment(body[pos:fm_.start()], transform))
            out.append(fm_.group(0))
            pos = fm_.end()
        out.append(self.text_segment(body[pos:], transform))
        text = "".join(out).strip("\n")
        # 本文の先頭にタイトルと同じ見出しがあれば消す（テーマがタイトルを表示するため）
        first, _, rest = text.partition("\n")
        if re.sub(r"\s+", "", first.lstrip("# ")) == re.sub(r"\s+", "", title) and first.startswith("#"):
            text = rest.lstrip("\n")
        return text + "\n"

    @staticmethod
    def text_segment(seg, transform):
        codes = []

        def stash(m):
            codes.append(m.group(0))
            return f"{len(codes) - 1}"

        seg = INLINE_CODE_RE.sub(stash, seg)
        seg = transform(seg)
        return re.sub(r"(\d+)", lambda m: codes[int(m.group(1))], seg)

    # ---- 全体 ----
    def run(self):
        r = self.report
        if not self.vault.notes:
            r.error("vault にノートが見つかりません。同期を中止しました。")
            return r
        published, errors_slugs, unreadable = {}, set(), set()
        for rel, (fm, _body) in sorted(self.vault.notes.items()):
            if YAML_ERROR_KEY in fm:
                msg, raw = fm[YAML_ERROR_KEY]
                if re.search(r"^publish:\s*true\b", raw, re.M | re.I):
                    r.error(f"{rel}: 設定欄を読み取れないため公開できません（{msg[:120]}）。"
                            "値に : や # が入るときは、全体を引用符 \" で囲んでください")
                    sm = re.search(r"^slug:\s*[\"']?([^\s\"']+)", raw, re.M)
                    if sm:
                        unreadable.add(sm.group(1))
                continue
            if not is_true(fm.get("publish")):
                continue
            slug = str(fm.get("slug") or "").strip()
            if not SLUG_RE.match(slug):
                r.error(f"{rel}: slug が未設定か不正です（半角小文字・数字・ハイフンで最大6語）: {slug!r}")
                continue
            if slug in published:
                r.error(f"{rel}: slug「{slug}」が {published[slug]['rel']} と重複しています")
                errors_slugs.add(slug)
                continue
            published[slug] = {"rel": rel, "fm": fm}
        for slug in errors_slugs:
            published.pop(slug, None)
        protected = set(published) | unreadable | {
            str(fm.get("slug")).strip() for fm, _ in self.vault.notes.values()
            if is_true(fm.get("publish")) and fm.get("slug")
        }

        managed = self.managed_posts()
        # slug が既存の（管理外の）記事とぶつからないか
        for slug in list(published):
            clash = os.path.join(self.posts_dir, slug)
            if slug not in managed and (os.path.exists(clash) or os.path.exists(clash + ".md")):
                r.error(f"{published[slug]['rel']}: slug「{slug}」は既存の記事のフォルダ名と重複しています")
                del published[slug]

        # 日付・題名・URL を先に決める（記事どうしのリンクに使う）
        pub_index = {}
        for slug, p in published.items():
            fm = p["fm"]
            try:
                if fm.get("date"):
                    d = to_jst(fm["date"])
                elif managed.get(slug):
                    d = to_jst(yaml.safe_load(managed[slug]))
                else:
                    d = self.now
            except (ValueError, TypeError) as e:
                r.error(f"{p['rel']}: date を読み取れません: {e}")
                continue
            title = str(fm.get("title") or os.path.splitext(os.path.basename(p["rel"]))[0]).strip()
            p.update(date=d, title=title, url=f"/{d.year:04d}/{d.month:02d}/{d.day:02d}/{slug}/")
            for key in {os.path.splitext(p["rel"])[0].lower(), os.path.splitext(os.path.basename(p["rel"]))[0].lower()}:
                pub_index.setdefault(key, p)
        published = {s: p for s, p in published.items() if "url" in p}

        for slug, p in published.items():
            rel, fm = p["rel"], p["fm"]
            body = self.vault.notes[rel][1]
            text = self.convert_body(rel, slug, p["title"], body, pub_index)
            tags = fm.get("tags") or []
            if isinstance(tags, str):
                tags = [t for t in re.split(r"[,\s]+", tags) if t]
            tags = [str(t).lstrip("#").replace("/", "-") for t in tags if str(t).strip()]
            head = ["---", f"title: {q(p['title'])}", f"date: {iso(p['date'])}"]
            lm = git_lastmod(self.vault.root, rel)
            if lm and lm > p["date"]:
                head.append(f"lastmod: {iso(lm)}")
            head.append(f"slug: {q(slug)}")
            if tags:
                head.append("tags: [" + ", ".join(q(t) for t in tags) + "]")
            lang = str(fm.get("lang") or "").strip().lower()
            if lang in LANGS and lang != "ja":
                head.append(f"htmlLang: {lang}")
            head += [f"obsidianSource: {q(rel)}", "---", ""]
            new_text = "\n".join(head) + "\n" + text
            path = os.path.join(self.posts_dir, slug, "index.md")
            if os.path.isfile(path) and strip_lastmod(open(path, encoding="utf-8").read()) == strip_lastmod(new_text):
                changed = False  # 中身が同じなら、更新日だけを変えて再公開しない
            else:
                changed = self.write(path, new_text)
            if slug not in managed:
                r.created.append(slug)
            elif changed:
                r.updated.append(slug)

        # 公開を止めた記事を消す
        gone = [s for s in managed if s not in protected]
        if len(gone) > MAX_REMOVALS and not self.allow_many:
            r.error(f"一度に {len(gone)} 本を削除しようとしたため中止しました（{', '.join(gone)}）。"
                    "意図したものなら --allow-remove-many を付けて実行してください。")
        else:
            for slug in gone:
                if not self.dry:
                    shutil.rmtree(os.path.join(self.posts_dir, slug))
                    self.state["images"] = {k: v for k, v in self.state["images"].items() if not k.startswith(slug + "/")}
                r.removed.append(slug)

        if not self.dry:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            with open(self.state_path, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(self.state, fh, ensure_ascii=False, indent=1, sort_keys=True)
                fh.write("\n")
        return r


def strip_lastmod(text):
    return re.sub(r"(?m)^lastmod: .*\n", "", text, count=1)


def harden_breaks(text):
    """Obsidian は改行をそのまま改行として表示するので、Hugo でも同じになるよう段落内の改行を強制改行にする。"""
    lines = text.split("\n")
    block = re.compile(r"^(#{1,6}\s|[-*+]\s|\d+[.)]\s|\||```|~~~|<|---+$|\*\*\*+$|___+$|\[\^[^\]]+\]:)")
    for i in range(len(lines) - 1):
        cur, nxt = lines[i], lines[i + 1]
        if not cur.strip() or not nxt.strip() or cur.endswith("  ") or cur.endswith("\\"):
            continue
        if nxt[0] in " \t" or block.match(cur) or block.match(nxt):
            continue
        if re.match(r">\s*\[!", cur):  # 囲み枠の見出し行は Hugo が解釈するので触らない
            continue
        if cur.startswith(">") != nxt.startswith(">"):
            continue
        lines[i] = cur + "  "
    return "\n".join(lines)


def write_summary(r):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    rows = [("追加", r.created), ("更新", r.updated), ("公開停止", r.removed)]
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("## Obsidian 同期\n\n")
        for label, items in rows:
            fh.write(f"- {label}: {', '.join(items) if items else 'なし'}\n")
        for label, items in (("警告", r.warnings), ("エラー", r.errors)):
            if items:
                fh.write(f"\n### {label}\n" + "".join(f"- {x}\n" for x in items))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vault", required=True, help="Obsidian の vault（リポジトリ）のパス")
    ap.add_argument("--site", default=".", help="Hugo サイトのパス")
    ap.add_argument("--dry-run", action="store_true", help="書き込まず、結果だけ表示する")
    ap.add_argument("--allow-remove-many", action="store_true", help="一度に3本以上の削除を許可する")
    a = ap.parse_args()
    r = Sync(os.path.abspath(a.vault), os.path.abspath(a.site), a.dry_run, a.allow_remove_many).run()
    print(f"追加 {len(r.created)} / 更新 {len(r.updated)} / 公開停止 {len(r.removed)} / 警告 {len(r.warnings)} / エラー {len(r.errors)}")
    write_summary(r)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
            fh.write(f"errors={len(r.errors)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
