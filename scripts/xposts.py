#!/usr/bin/env python3
"""記事の中の X（旧 Twitter）のポストを、「埋め込み + 投稿者・ID・本文の控え」に書き換える。

    python scripts/xposts.py fetch     # oEmbed でポストの情報を取得し、scripts/xposts_cache.json に保存する（要インターネット）
    python scripts/xposts.py convert   # 取得済みの情報で、content/ の記事を書き換える
    python scripts/xposts.py report    # 取得できなかった・書き換えられなかったポストを一覧する

書き換え後の記事には、次の shortcode（layouts/shortcodes/xpost.html）が入る。

    {{< xpost url="https://twitter.com/ID/status/123" name="表示名" id="ID" date="2018-07-19" >}}
    ポストの本文
    {{< /xpost >}}

控えは記事のファイルに書き込む（ポストが消えても記事に残る）。cache は取得の記録で、Hugo は読まない。
取得できなかったポスト（削除・非公開など）は書き換えず、元の URL のままにする。
cache の `status` を "ok" にして name / handle / text / date を手で書けば、手書きの控えとして使える。
"""
import argparse
import datetime
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

STATUS_URL = (
    r"https?://(?:www\.|mobile\.)?(?:twitter\.com|x\.com)/[^/\s?#)<>\"\]]+/status(?:es)?/\d+(?:[/?#][^\s)<>\"\]]*)?"
)
STATUS_RE = re.compile(
    r"https?://(?:www\.|mobile\.)?(?:twitter\.com|x\.com)/(?P<user>[^/\s?#)<>\"\]]+)/status(?:es)?/(?P<id>\d+)(?:[/?#][^\s)<>\"\]]*)?",
    re.I,
)
FENCE_RE = re.compile(r"^(?P<f>```|~~~)[^\n]*\n.*?^(?P=f)[ \t]*$", re.S | re.M)
OEMBED = os.environ.get("XPOSTS_OEMBED_URL", "https://publish.twitter.com/oembed")  # テストでだけ差し替える
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}
USER_AGENT = "Mozilla/5.0 (compatible; hugo-xposts-archiver)"
DEFAULT_CACHE = os.path.join("scripts", "xposts_cache.json")


def tweet_id(url):
    m = STATUS_RE.search(url)
    return m.group("id") if m else None


def canonical_url(url):
    m = STATUS_RE.search(url)
    return f"https://twitter.com/{m.group('user')}/status/{m.group('id')}" if m else url


# ---------- oEmbed の取得と解析 ----------
def parse_date(s):
    s = s.strip()
    m = re.match(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", s)
    if m:
        return f"{int(m[1]):04d}-{int(m[2]):02d}-{int(m[3]):02d}"
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", s)
    if m and m[1].lower() in MONTHS:
        return f"{int(m[3]):04d}-{MONTHS[m[1].lower()]:02d}-{int(m[2]):02d}"
    return None


def link_media(text):
    """画像・動画へのリンクは `pic.twitter.com/…` と、スキームなしで入るので、リンクにできるよう https:// を付ける。"""
    return re.sub(r"(?<![/\w.])(pic\.(?:twitter|x)\.com/[A-Za-z0-9]+)", r"https://\1", text)


def parse_oembed(data, url):
    """oEmbed の応答（dict）から、控えに使う情報を取り出す。読み取れなければ None。"""
    markup = data.get("html") or ""
    m = re.search(r"<p[^>]*>(.*?)</p>", markup, re.S)
    if not m:
        return None
    body = re.sub(r"<br\s*/?>", "\n", m.group(1))
    body = re.sub(r"<a\b[^>]*>(.*?)</a>", r"\1", body, flags=re.S)
    body = re.sub(r"<[^>]+>", "", body)
    text = html.unescape(body).replace("\r", "").strip()
    text = link_media(text)
    handle = ""
    hm = re.match(r"https?://(?:www\.)?(?:twitter|x)\.com/([^/?#]+)", data.get("author_url") or "")
    if hm:
        handle = hm.group(1)
    elif STATUS_RE.search(url):
        handle = STATUS_RE.search(url).group("user")
    dates = re.findall(r"<a\b[^>]*>([^<]+)</a>\s*</blockquote>", markup)
    return {
        "url": canonical_url(url),
        "handle": handle,
        "name": html.unescape(data.get("author_name") or ""),
        "date": parse_date(dates[-1]) if dates else None,
        "text": text,
        "status": "ok",
    }


def fetch_oembed(url, retries=3, timeout=20):
    """('ok', 情報) / ('unavailable', None)（削除・非公開など）/ ('error', 理由)（通信の失敗。再試行できる）"""
    query = urllib.parse.urlencode(
        {"url": canonical_url(url), "omit_script": "1", "dnt": "1", "hide_thread": "1"})
    req = urllib.request.Request(f"{OEMBED}?{query}", headers={"User-Agent": USER_AGENT})
    reason = "unknown"
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                entry = parse_oembed(json.load(resp), url)
            return ("ok", entry) if entry else ("error", "oEmbed の応答を読み取れませんでした")
        except urllib.error.HTTPError as e:
            if e.code in (403, 404, 410):
                return "unavailable", None
            reason = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            reason = str(getattr(e, "reason", e))
        time.sleep(2 * (attempt + 1))
    return "error", reason


class Cache:
    """ポストの情報の記録（scripts/xposts_cache.json）。"""

    def __init__(self, path):
        self.path = path
        self.dirty = False
        try:
            with open(path, encoding="utf-8") as fh:
                self.data = json.load(fh)
        except (OSError, ValueError):
            self.data = {}

    def get(self, url, fetch=True):
        """記録にある情報を返す。なければ取得を試す。取得に失敗したら (None, 理由)。"""
        tid = tweet_id(url)
        if tid in self.data:
            return self.data[tid], None
        if not fetch:
            return None, "未取得"
        status, result = fetch_oembed(url)
        if status == "ok":
            result["checked"] = datetime.date.today().isoformat()
            self.data[tid] = result
        elif status == "unavailable":
            self.data[tid] = {"url": canonical_url(url), "status": "unavailable",
                              "checked": datetime.date.today().isoformat()}
        else:
            return None, result
        self.dirty = True
        return self.data[tid], None

    def save(self):
        if not self.dirty:
            return
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(self.path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(self.data, fh, ensure_ascii=False, indent=1, sort_keys=True)
            fh.write("\n")


# ---------- shortcode ----------
def hq(s):
    """Hugo の shortcode の引数として安全な形に引用符で囲む。"""
    s = s.replace("\n", " ")
    if '"' not in s and "\\" not in s:
        return f'"{s}"'
    if "`" not in s:
        return f"`{s}`"
    return '"' + s.replace("\\", "").replace('"', "'").replace("`", "'") + '"'


def shortcode(url, entry):
    """記録の情報から shortcode を作る。情報がなければ、埋め込みだけの shortcode にする。"""
    url = canonical_url(url)
    if not entry or entry.get("status") != "ok" or not entry.get("text"):
        return f"{{{{< xpost url={hq(url)} />}}}}"
    args = [f"url={hq(entry.get('url') or url)}"]
    if entry.get("name"):
        args.append(f"name={hq(entry['name'])}")
    if entry.get("handle"):
        args.append(f"id={hq(entry['handle'])}")
    if entry.get("date"):
        args.append(f"date={hq(entry['date'])}")
    text = entry["text"].replace("{{<", "{ {<").replace("{{%", "{ {%").strip("\n")  # shortcode の記号だけ避ける
    return "{{< xpost " + " ".join(args) + " >}}\n" + text + "\n{{< /xpost >}}"


# ---------- 記事の走査と書き換え ----------
def iter_plain_lines(text):
    """コードブロックと、xpost の shortcode の中を除いた (行番号, 行) を返す。"""
    fences = [(m.start(), m.end()) for m in FENCE_RE.finditer(text)]
    pos, in_block = 0, False
    for no, line in enumerate(text.split("\n")):
        start, pos = pos, pos + len(line) + 1
        if any(a <= start < b for a, b in fences):
            continue
        if in_block:
            in_block = "{{< /xpost >}}" not in line
            continue
        if "{{< xpost" in line:
            in_block = "/>}}" not in line and ">}}" in line and "{{< /xpost" not in line
            continue
        yield no, line


def find_urls(text):
    return [m.group(0) for _, ln in iter_plain_lines(text) for m in STATUS_RE.finditer(ln)]


LEAD_RE = re.compile(r"^(?:>|[-*+]\s|\||#{1,6}\s|<|\d+[.)]\s|\s)")  # 引用・箇条書き・表・見出し・HTML・字下げ
AUTOLINK_RE = re.compile(r"<?(" + STATUS_URL + r")>?", re.I)


def convert_text(text, cache, stats):
    """行が「ポストの URL だけ」または「文章 + URL」のときに、URL を shortcode に書き換える。
    取得できていないポストの URL はそのまま残す。関係のない行は変えない。"""
    lines = text.split("\n")
    plain = {no for no, _ in iter_plain_lines(text)}
    out, blank_after = [], False
    for no, line in enumerate(lines):
        if blank_after and line.strip():
            out.append("")
        blank_after = False
        matches = list(AUTOLINK_RE.finditer(line)) if no in plain else []
        if not matches:
            out.append(line)
            continue
        solo = AUTOLINK_RE.fullmatch(line.strip()) is not None
        if not solo and (LEAD_RE.match(line) or re.search(r"\]\(|href=|src=", line)):
            stats["manual"].append((matches[0].group(1), "リンク・引用・箇条書きなどの中にあるため、手で直してください"))
            out.append(line)
            continue
        entries = [cache.get(m.group(1), fetch=False)[0] for m in matches]
        usable = [bool(e and e.get("status") == "ok" and e.get("text")) for e in entries]
        for m, e, ok in zip(matches, entries, usable):
            if not ok:
                stats["unavailable" if e and e.get("status") == "unavailable" else "unfetched"].append(m.group(1))
        if not any(usable):
            out.append(line)
            continue
        pieces, last = [], 0
        for m, e, ok in zip(matches, entries, usable):
            before = line[last:m.start()].strip()
            if before:
                pieces.append(before)
            if ok:
                pieces.append(shortcode(m.group(1), e))
                stats["converted"] += 1
            else:
                pieces.append(m.group(1))
            last = m.end()
        tail = line[last:].strip()
        if tail:
            pieces.append(tail)
        if out and out[-1].strip():
            out.append("")
        out.append("\n\n".join(pieces))
        blank_after = True
    return "\n".join(out)


def content_files(root):
    for dp, dn, fn in os.walk(root):
        dn.sort()
        for f in sorted(fn):
            if f.endswith(".md"):
                yield os.path.join(dp, f)


def write_summary(lines):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


def cmd_fetch(a):
    cache = Cache(a.cache)
    urls = {}
    for f in content_files(a.content):
        for u in find_urls(open(f, encoding="utf-8").read()):
            urls.setdefault(tweet_id(u), u)
    todo = [(i, u) for i, u in urls.items()
            if i not in cache.data or (a.retry_unavailable and cache.data[i].get("status") == "unavailable")]
    if a.retry_unavailable:
        for i, _ in todo:
            cache.data.pop(i, None)
    print(f"記事にあるポスト {len(urls)} 件 / 取得する {len(todo)} 件")
    errors, ok, gone = [], 0, 0
    for n, (i, u) in enumerate(todo):
        entry, why = cache.get(u)
        if entry is None:
            errors.append((u, why))
            print(f"::warning::取得に失敗しました（あとでもう一度試せます）: {u} ({why})")
        elif entry.get("status") == "ok":
            ok += 1
        else:
            gone += 1
            print(f"::warning::取得できません（削除・非公開など）: {u}")
        if n < len(todo) - 1:
            time.sleep(0.5)
    cache.save()
    write_summary([f"## X のポストの取得", f"- 取得できた: {ok}", f"- 取得できない（削除・非公開など）: {gone}",
                   f"- 通信の失敗: {len(errors)}"] + [f"  - {u}: {w}" for u, w in errors])
    print(f"取得できた {ok} / 取得できない {gone} / 通信の失敗 {len(errors)}")
    return 1 if todo and len(errors) == len(todo) else 0


def cmd_convert(a):
    cache = Cache(a.cache)
    stats = {"converted": 0, "unavailable": [], "unfetched": [], "manual": []}
    changed = 0
    for f in content_files(a.content):
        text = open(f, encoding="utf-8").read()
        new = convert_text(text, cache, stats)
        if new != text:
            changed += 1
            if not a.dry_run:
                with open(f, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(new)
            print(f"書き換え: {f}")
    print(f"ポスト {stats['converted']} 件を書き換え（{changed} 記事）/ 取得できず残した {len(stats['unavailable'])} / "
          f"未取得 {len(stats['unfetched'])} / 手で直す {len(stats['manual'])}")
    lines = ["## X のポストの書き換え", f"- 書き換えたポスト: {stats['converted']}（{changed} 記事）"]
    for label, key in (("取得できず、元の URL のままにしたポスト", "unavailable"), ("まだ取得していないポスト", "unfetched")):
        if stats[key]:
            lines += [f"- {label}: {len(stats[key])}"] + [f"  - {u}" for u in stats[key]]
    if stats["manual"]:
        lines += [f"- 手で直すもの: {len(stats['manual'])}"] + [f"  - {u}（{w}）" for u, w in stats["manual"]]
    write_summary(lines)
    return 0


def cmd_report(a):
    cache = Cache(a.cache)
    todo = {"未取得": [], "取得できない": []}
    for f in content_files(a.content):
        for u in find_urls(open(f, encoding="utf-8").read()):
            e = cache.data.get(tweet_id(u))
            if not e:
                todo["未取得"].append((f, u))
            elif e.get("status") != "ok":
                todo["取得できない"].append((f, u))
    for k, items in todo.items():
        print(f"{k}: {len(items)}")
        for f, u in items:
            print(f"  {f}: {u}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["fetch", "convert", "report"])
    ap.add_argument("--content", default="content")
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    ap.add_argument("--retry-unavailable", action="store_true", help="fetch: 取得できなかったポストをもう一度試す")
    ap.add_argument("--dry-run", action="store_true", help="convert: 書き込まない")
    a = ap.parse_args()
    return {"fetch": cmd_fetch, "convert": cmd_convert, "report": cmd_report}[a.command](a)


if __name__ == "__main__":
    sys.exit(main())
