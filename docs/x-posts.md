# 記事の中の X のポスト

記事の中の X（旧 Twitter）のポストは、**埋め込み**と、その下の**投稿者・ID・日付・本文の控え**の形で表示されます。
控えは記事のファイルに書き込まれるので、元のポストが消えたり、アカウントが変わったりしても、記事には残ります。

```
{{< xpost url="https://twitter.com/ID/status/123" name="表示名" id="ID" date="2018-07-19" >}}
ポストの本文
{{< /xpost >}}
```

- 埋め込みは X の公式の埋め込み（`platform.twitter.com` の `widgets.js`）です。読み込まれないとき（広告ブロックなど）は、
  ポストへのリンクが表示されます。トラッキングを減らすため、Do Not Track の指定を付けています。
- 名前・ID・日付・本文を省いて `{{< xpost url="…" />}}` と書くと、埋め込みだけを出します。
- 表示は `layouts/shortcodes/xpost.html`、見た目は `assets/custom.css` の `.xpost` です。

## 今ある記事を書き換える（一度だけ）

1. GitHub の **Actions → Archive X posts → Run workflow** を押します。
2. 記事の中のポスト（`twitter.com` / `x.com` の `/status/…` の URL）を、X の oEmbed から取得して、
   `scripts/xposts_cache.json` に記録し、記事を書き換えます。
3. 結果は `main` ではなく、新しいブランチ `xposts-archive-<番号>` に push されます。実行の **Summary** に、
   書き換えた数と、取得できなかったポストの一覧が出ます。
4. 差分を確認して、プルリクエストでマージします。

書き換えるのは次の形のものです。

| 記事の中の形 | 書き換え |
|---|---|
| ポストの URL だけの行（`<URL>` の形も含む） | その行を shortcode にします |
| 文章のあとに、ポストの URL が並んでいる行 | 文章はそのままにして、URL を shortcode にします（段落が分かれます） |
| 箇条書き・引用・表・リンク記法（`[文字](URL)`）・HTML の中にあるもの | 変えません。Summary に「手で直すもの」として出ます |
| コードブロックの中 | 変えません |

- **取得できなかったポスト：** 削除・非公開・アカウントの停止などで、oEmbed が応答しないポストは、元の URL のまま残ります。
  Summary に一覧が出ます。控えを手で書く場合は、`scripts/xposts_cache.json` の該当の ID の項目に
  `"status": "ok"`、`"name"`、`"handle"`、`"date"`（`YYYY-MM-DD`）、`"text"`、`"url"` を書いて、
  `python scripts/xposts.py convert` を実行します。
- **通信の失敗：** 一時的な失敗は記録されません。もう一度 Run workflow を押すと、取得できなかった分だけ試します。
- **取得できなかった分をもう一度試す：** Run workflow の「`retry_unavailable`」にチェックを入れます。
- もう一度実行しても、すでに書き換えた分は変わりません（何度実行しても安全です）。

手元で実行するとき（インターネットが必要）：

```
python scripts/xposts.py fetch      # 取得して scripts/xposts_cache.json に記録
python scripts/xposts.py convert    # 記事を書き換える（--dry-run で書き込まずに確認）
python scripts/xposts.py report     # 取得できていないポストの一覧
```

## Obsidian で書く記事

ノートに次のどちらかの形で書くと、同期のときに、同じ形（埋め込み + 控え）になります。

```
![](https://x.com/ユーザー名/status/123456789)

https://x.com/ユーザー名/status/123456789
```

- 2つ目の形は、**行の先頭から URL だけを書いた行**に限ります（箇条書きの中などは変換しません）。
- 同期の Actions が、ポストの情報を取得して、控えを記事に書き込みます。取得した情報は `scripts/xposts_cache.json`
  に記録されるので、同じポストを何度も取りにいきません。
- 削除されたポストは、リンクだけになります。通信に失敗したときは、埋め込みだけを載せて、次の同期でもう一度試します
  （どちらも Actions の警告に出ます）。

## 注意
- **他の人のポスト：** 本文をそのまま載せるので、投稿者名・ID・元のポストへのリンクを必ず付けています（引用の形です）。
  載せたくないポストは、shortcode ごと消すか、リンクに戻してください。
- **控えの内容：** oEmbed の本文は、URL が `t.co` の短縮 URL になります。画像や動画は、控えには入りません（埋め込みには出ます）。
- X の仕様や API の都合で、oEmbed が使えなくなると、新しい取得はできなくなります（すでに記録した控えは残ります）。
