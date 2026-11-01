# Photos（Flickr のアルバム）

`/photos/` には、Flickr で公開しているアルバムが並びます。アルバムごとに、Flickr での並び順のまま、写真が表示されます。
各写真は Flickr の写真ページにリンクしています（Flickr の利用規約で必要なリンクです）。

## 仕組み
API キーは使わず、Flickr の公開ページ（アルバムの一覧と、アルバムのスライドショーのページ）に埋め込まれているデータを読みます。

```
Flickr の公開アルバム ──▶ scripts/flickr_sync.py ──▶ content/photos/<slug>/index.md ──▶ サイト
```

- アルバムのファイルには、写真ごとに、Flickr の写真ページの URL・画像の URL・大きさが書き込まれます。
  サイトを作るときに Flickr へ問い合わせないので、速く、制限にもかかりません。
- 画像は Flickr のサーバーから直接表示します（1024px の大きさ）。
- アルバムの日付は、アルバムの中の写真の、**いちばん古い撮影日**です（一覧は新しい順）。
- この仕組みで作ったアルバムには `flickrAlbumId` が付きます。付いていない手書きのアルバムは、読み書きも削除もしません。

## アルバムを増やす・直す
1. Flickr でアルバムを作る（または写真を足す・並べ替える）。公開（Public）にしておきます。
2. GitHub の **Actions → Sync Flickr albums → Run workflow** を押します。
3. 数分で、サイトに反映されます。結果は実行の **Summary** に出ます（追加・更新・削除したアルバム、警告、エラー）。

新しいアルバムは、はじめは URL が `/photos/album-<ID>/` になり、Summary に警告が出ます。
URL の名前（slug）を決めて、`scripts/flickr_albums.json` に書いてください。

```json
"72177720335479701": { "slug": "kamata-matsuri-2026", "title": "蒲田祭り2026" }
```

- slug は、タイトルを英訳した冒頭の数語を、小文字・ハイフンでつないだもの（半角の小文字・数字・ハイフン、最大8語）にします。
- `title` は見分けるためのメモです（サイトには使いません）。
- **slug を変えると、URL が変わります。** 公開したあとは、変えないでください。

## 設定（`scripts/flickr_albums.json`）
| 項目 | 内容 |
|---|---|
| `user` | Flickr のユーザー名（`walterinsect`） |
| `albums.<ID>.slug` | URL の名前 |
| `albums.<ID>.captions` | `auto`（既定）/ `all` / `none` |
| `albums.<ID>.hidden` | `true` にすると、そのアルバムを載せません |

### 写真のキャプション
写真の題名（Flickr のタイトル）が、キャプションになります。
- `auto`（既定）：カメラのファイル名のようなもの（`2025_0429_…`、`IMG_…` など）と、同じ題名が3枚以上あるもの（グループの名前とみなします）は出しません。
- `all`：ファイル名のようなもの以外を、すべて出します。
- `none`：出しません。

## 消えるもの
Flickr で非公開にした・削除したアルバムは、サイトからも消えます。一度に消せるのは2つまでです（それ以上は、事故を防ぐために同期を止めます）。
意図したものなら、`python scripts/flickr_sync.py --allow-remove-many` を手元で実行してください。

## 手で書くアルバム・記事の中の写真
- `content/photos/<名前>/index.md` に、`photos:` として Flickr の写真ページの URL を並べると、手書きのアルバムも作れます
  （画像は、サイトを作るときに Flickr の oEmbed で取得します）。
- ブログの記事の中に、写真を1枚載せるには `{{< flickr url="https://www.flickr.com/photos/…/写真ID/" >}}` と書きます。

## 注意
- Flickr のページの形式が変わると、取り込みが失敗することがあります（Actions にエラーが出ます。サイトは変わりません）。
- 手元で実行するには、インターネットが必要です：`python scripts/flickr_sync.py`（`--dry-run` で、書き込まずに確認できます）。
