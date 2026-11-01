#!/usr/bin/env python3
"""Flickr の公開アルバムを、Photos セクション（content/photos/<slug>/index.md）に取り込む。API キーは使わない。

    python scripts/flickr_sync.py              # 取り込む（インターネットが必要）
    python scripts/flickr_sync.py --dry-run    # 書き込まずに、結果だけを表示する

Flickr の公開ページ（アルバム一覧と、アルバムのスライドショーのページ）に埋め込まれているデータを読む。
- アルバム一覧:  https://www.flickr.com/photos/<ユーザー>/albums
- アルバムの全写真:  https://www.flickr.com/photos/<ユーザー>/albums/<アルバムID>/with/<写真ID>
画像の URL・大きさもここから取るので、サイトを作るときに Flickr へ問い合わせる必要はない。

- アルバムの URL の名前（slug）は scripts/flickr_albums.json に書く（アルバムID → slug）。
  書いていない新しいアルバムは `album-<ID>` になり、警告を出す。slug を決めて書き足すと、次の同期で URL が変わる。
- この仕組みで作ったアルバムには `flickrAlbumId` が付く。付いていないもの（手書きのアルバム）は、読み書きも削除もしない。
- Flickr で公開をやめた・消したアルバムは、サイトからも消す（一度に消せるのは2つまで）。
- 写真の題名（キャプション）は、既定では「ファイル名のようなもの」「同じ題名が3枚以上あるもの」を出さない。
  アルバムごとに `"captions": "all"`（ファイル名以外すべて）/ `"none"`（出さない）に変えられる（flickr_albums.json）。
"""
import argparse
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

FLICKR = "https://www.flickr.com"
USER_AGENT = "Mozilla/5.0 (compatible; hugo-flickr-sync)"
JST = datetime.timezone(datetime.timedelta(hours=9))
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+){0,7}$")
SIZE_ORDER = ["l", "c", "z", "m"]  # 表示に使う画像の大きさ（1024px → 800px …）
MAX_REMOVALS = 2
CAMERA_TITLE = re.compile(
    r"^(?:IMG|DSC[FN]?|PXL|P\d|DJI|GOPR|MVI|SAM|_?DSC)[_\-\s]?\d|^\d{4}[_\-]\d{4}[_\-]\d+|^\d{6,}|^Screenshot", re.I)
REPEAT_LIMIT = 3  # 同じ題名がこの回数以上あるものは、グループの名前とみなして表示しない（auto のとき）


def http_get(url, retries=3, timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.8"})
    reason = "unknown"
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (403, 404, 410):
                raise SystemExit(f"取得できません（HTTP {e.code}）: {url}")
            reason = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            reason = str(getattr(e, "reason", e))
        time.sleep(2 * (attempt + 1))
    raise SystemExit(f"取得に失敗しました（{reason}）: {url}")


def unwrap(o):
    """Flickr のデータ（{data, exportMetaType} や {_data: [...]} の入れ子）を、普通の dict / list にする。"""
    if isinstance(o, dict):
        if "data" in o and "exportMetaType" in o:
            return unwrap(o["data"])
        if "_data" in o:
            return [unwrap(x) for x in o["_data"]]
        return {k: unwrap(v) for k, v in o.items()}
    if isinstance(o, list):
        return [unwrap(x) for x in o]
    return o


def model_export(page):
    """ページに埋め込まれている `modelExport` の JSON を取り出す。"""
    m = re.search(r"modelExport:\s*(\{)", page)
    if not m:
        raise SystemExit("Flickr のページの形式が変わったようです（modelExport が見つかりません）。スクリプトの見直しが必要です。")
    start, depth, in_str, esc = m.start(1), 0, False, False
    for i in range(start, len(page)):
        c = page[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return unwrap(json.loads(page[start:i + 1])["main"])
    raise SystemExit("Flickr のページのデータを読み取れませんでした。")


def abs_url(u):
    return "https:" + u if u.startswith("//") else u


def list_albums(user):
    albums, seen = [], set()
    for page in range(1, 21):
        url = f"{FLICKR}/photos/{user}/albums" + ("" if page == 1 else f"/page{page}")
        main = model_export(http_get(url))
        models = main.get("sets-models") or []
        if not models:
            raise SystemExit(f"アルバムの一覧を読み取れませんでした: {url}")
        total = models[0].get("totalItems", 0)
        new = [a for a in models[0].get("albumsList", []) if a["id"] not in seen]
        if not new:
            break
        for a in new:
            seen.add(a["id"])
            albums.append(a)
        if len(albums) >= total:
            break
        time.sleep(1)
    return albums


def album_photos(user, album):
    """アルバムの全写真（Flickr のアルバムでの並び順）。"""
    url = f"{FLICKR}/photos/{user}/albums/{album['id']}/with/{album['primary']}"
    main = model_export(http_get(url))
    sets = main.get("set-models") or []
    if not sets:
        raise SystemExit(f"アルバムの写真を読み取れませんでした: {url}")
    return sets[0].get("photoPageList") or []


def size_of(sizes):
    for key in SIZE_ORDER:
        if key in sizes:
            s = sizes[key]
            return abs_url(s.get("displayUrl") or s["url"]), int(s["width"]), int(s["height"])
    key = max(sizes, key=lambda k: sizes[k].get("width", 0))
    s = sizes[key]
    return abs_url(s.get("displayUrl") or s["url"]), int(s["width"]), int(s["height"])


def q(s):
    return json.dumps(s, ensure_ascii=False)


def meaningful_title(title, album_title):
    """写真の題名のうち、キャプションとして意味があるものだけ残す（アルバム名と同じ・カメラの自動名は捨てる）。"""
    t = (title or "").strip()
    if not t or t == album_title or CAMERA_TITLE.match(t):
        return ""
    return t


def render(user, album, photos, slug, captions="auto"):
    """captions: "auto"（ファイル名・繰り返しの題名は出さない）/ "all"（ファイル名以外すべて）/ "none"。"""
    rows, taken = [], []
    titles = [meaningful_title(p.get("title"), album["title"]) for p in photos]
    repeats = {t: titles.count(t) for t in set(titles)}
    for p, title in zip(photos, titles):
        src, w, h = size_of(p["sizes"])
        caption = "" if captions == "none" else title
        if captions == "auto" and repeats.get(title, 0) >= REPEAT_LIMIT:
            caption = ""
        t = (p.get("stats") or {}).get("dateTaken")
        if t:
            taken.append(t)
        page_url = f"{FLICKR}/photos/{user}/{p['id']}/"
        row = [f"  - url: {q(page_url)}", f"    src: {q(src)}", f"    width: {w}", f"    height: {h}"]
        if caption:
            row.append(f"    caption: {q(caption)}")
        rows.append("\n".join(row))
    day = min(taken)[:10] if taken else datetime.date.today().isoformat()
    cover_src, cover_w, cover_h = size_of(album["primaryPhotoSizes"])
    album_url = f"{FLICKR}/photos/{user}/albums/{album['id']}/"
    head = [
        "---",
        f"title: {q(album['title'])}",
        f"date: {day}T00:00:00+09:00",
        f"flickrAlbumId: {q(album['id'])}",
        f"flickr_album: {q(album_url)}",
        f"coverSrc: {q(cover_src)}",
        f"coverWidth: {cover_w}",
        f"coverHeight: {cover_h}",
        "photos:",
    ]
    return "\n".join(head + rows + ["---", ""])


def managed_albums(photos_dir):
    out = {}
    if not os.path.isdir(photos_dir):
        return out
    for name in sorted(os.listdir(photos_dir)):
        idx = os.path.join(photos_dir, name, "index.md")
        if os.path.isfile(idx):
            m = re.search(r'^flickrAlbumId:\s*"?(\d+)"?', open(idx, encoding="utf-8").read(), re.M)
            if m:
                out[m.group(1)] = name
    return out


def write_summary(lines):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.join("scripts", "flickr_albums.json"))
    ap.add_argument("--content", default=os.path.join("content", "photos"))
    ap.add_argument("--user", help="Flickr のユーザー名（省略すると設定ファイルの user）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-remove-many", action="store_true")
    a = ap.parse_args()

    try:
        config = json.load(open(a.config, encoding="utf-8"))
    except (OSError, ValueError):
        config = {}
    user = a.user or config.get("user") or "walterinsect"
    slugs = {k: v.get("slug") for k, v in (config.get("albums") or {}).items() if isinstance(v, dict)}
    caption_modes = {k: v.get("captions", "auto") for k, v in (config.get("albums") or {}).items() if isinstance(v, dict)}
    hidden = {k for k, v in (config.get("albums") or {}).items() if isinstance(v, dict) and v.get("hidden")}

    albums = [x for x in list_albums(user) if x["id"] not in hidden]
    print(f"Flickr の公開アルバム {len(albums)} 件（{user}）")
    existing = managed_albums(a.content)
    created, updated, warnings, errors, mismatch = [], [], [], [], []
    used = {}
    for n, album in enumerate(albums):
        aid = album["id"]
        slug = slugs.get(aid) or ""
        if not SLUG_RE.match(slug):
            if slug:
                errors.append(f"{album['title']}（{aid}）: slug「{slug}」が不正です（半角小文字・数字・ハイフン）")
            slug = f"album-{aid}"
            warnings.append(f"「{album['title']}」（{aid}）に slug が未設定です。scripts/flickr_albums.json に書くと、URL を /photos/<slug>/ にできます")
        if slug in used:
            errors.append(f"slug「{slug}」が「{used[slug]}」と重複しています（{album['title']}）")
            continue
        used[slug] = album["title"]
        time.sleep(1)
        photos = album_photos(user, album)
        if len(photos) != int(album.get("publicPhotosCount") or album.get("photoCount") or len(photos)):
            mismatch.append(f"「{album['title']}」: 写真 {len(photos)} 枚（Flickr の表示は {album.get('photoCount')} 枚）")
        text = render(user, album, photos, slug, caption_modes.get(aid, "auto"))
        path = os.path.join(a.content, slug, "index.md")
        old = existing.get(aid)
        if old and old != slug:  # slug を変えたとき
            if not a.dry_run:
                import shutil
                shutil.rmtree(os.path.join(a.content, old))
        if os.path.isfile(path) and not re.search(r"^flickrAlbumId:", open(path, encoding="utf-8").read(), re.M):
            errors.append(f"{path} は手書きのアルバムなので上書きしません（slug を変えるか、先に削除してください）")
            continue
        before = open(path, encoding="utf-8").read() if os.path.isfile(path) else None
        if before != text:
            (updated if before is not None else created).append(f"{slug}（{album['title']}・{len(photos)}枚）")
            if not a.dry_run:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
        print(f"  {slug:28} {len(photos):3} 枚  {album['title']}")

    keep = {aid for aid in (x['id'] for x in albums)}
    gone = [(aid, name) for aid, name in existing.items() if aid not in keep and name not in used]
    removed = []
    if len(gone) > MAX_REMOVALS and not a.allow_remove_many:
        errors.append(f"一度に {len(gone)} 件のアルバムを消そうとしたため中止しました。意図したものなら --allow-remove-many を付けてください")
    else:
        for aid, name in gone:
            if not a.dry_run:
                import shutil
                shutil.rmtree(os.path.join(a.content, name))
            removed.append(name)

    for w in warnings:
        print(f"::warning::{w}")
    for m in mismatch:
        print(f"::warning::{m}")
    for e in errors:
        print(f"::error::{e}")
    print(f"追加 {len(created)} / 更新 {len(updated)} / 削除 {len(removed)} / 警告 {len(warnings) + len(mismatch)} / エラー {len(errors)}")
    lines = ["## Flickr アルバムの同期", f"- 追加: {', '.join(created) or 'なし'}", f"- 更新: {', '.join(updated) or 'なし'}",
             f"- 削除: {', '.join(removed) or 'なし'}"]
    for label, items in (("警告", warnings + mismatch), ("エラー", errors)):
        if items:
            lines += [f"\n### {label}"] + [f"- {x}" for x in items]
    write_summary(lines)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
            fh.write(f"errors={len(errors)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
