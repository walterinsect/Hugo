# Obsidian で書いた記事を公開する

Obsidian のリポジトリ（[walterinsect/Obsidian](https://github.com/walterinsect/Obsidian)）のノートに
`publish: true` と書いて push すると、**1時間以内**にこのサイトの記事になります。
PC（GitHub Desktop）でも Android（GitSync）でも、やることは「書いて push」だけです。

```
Obsidian のノート ──push──▶ Obsidian リポジトリ
                                   │  毎時 17 分（または手動）
                                   ▼
                     GitHub Actions「Sync Obsidian posts」
                       ノートを変換 → ビルド確認 → main にコミット
                                   │
                                   ▼
                     GitHub Actions「Deploy Hugo site to Pages」 → 公開
```

公開するまでの下書きは Obsidian のリポジトリだけにあり、このリポジトリには入りません。

## ノートの書き方

ノートの先頭（設定欄）に、次を書きます。ノートはどのフォルダにあっても構いません。

```yaml
---
title: 記事のタイトル
slug: first-few-words
tags: [アニメ, 感想]
publish: true
---
```

| 項目 | 内容 |
|---|---|
| `publish` | `true` にした日から公開されます。`false` にするか、項目を消すと公開されません。 |
| `slug` | **必須。** 記事の URL の最後の部分です（下記）。 |
| `title` | 省略するとファイル名を使います。 |
| `tags` | タグ。`/` は `-` に置き換わります。 |
| `date` | 公開日。省略すると、**最初に取り込んだ日時**（日本時間）になり、以後は変わりません。日付を決めたいときだけ書きます（`2026-11-01` または `2026-11-01T10:00:00`）。 |
| `lang` | `en` または `fi` と書くと、そのページの言語指定が変わります（省略は日本語）。 |

`published`（Web Clipper などが付ける項目）とは別のものです。公開の印は `publish` です。

### slug の決め方
1. タイトルを英語にします。
2. `a` / `the` などの冠詞を除いて、先頭の3〜4語を使います。
3. 小文字にして、ハイフンでつなぎます（半角の小文字・数字・ハイフンのみ、最大6語）。

例：「A tribute to Penguin Highway」→ `tribute-penguin-highway`

記事の URL は `https://wyamazaki.fi/年/月/日/slug/` になります。slug が空・不正・他の記事と重複しているときは、
その記事だけ公開されず、Actions が失敗のお知らせを出します（他の記事は反映されます）。

### 新しいノートのひな形
Obsidian の `_templates/` に、次の内容で `Blog template.md` を作ると便利です。

```
---
title:
slug:
tags: []
publish: false
---
```

## 変換されるもの

| Obsidian の書き方 | 公開後 |
|---|---|
| `![[画像.png]]`、`![[画像.png\|300]]`、`![](画像のパス)` | 画像を記事のフォルダにコピーします。JPEG は位置情報（EXIF）を消して、長辺を 2000px 以下にします |
| `[[公開済みのノート]]`、`[[ノート\|別名]]` | その記事へのリンクになります |
| `[[公開していないノート]]` | リンクにせず、文字だけが残ります |
| `> [!note]`、`> [!warning] タイトル` など（囲み枠） | 囲み枠として表示します |
| `==ハイライト==` | ハイライト表示になります |
| `%%コメント%%`（複数行も可） | 公開されません（削除されます） |
| `![](https://x.com/…/status/…)`（X のポスト） | 「X のポスト」というリンクになります |
| 段落の中の改行 | Obsidian と同じく、改行として表示します |
| 本文の先頭のタイトルと同じ見出し | 重複するので取り除きます |
| `^ブロックID` | 取り除きます |

コードブロックとインラインコードの中は、そのままです。

**未対応のもの：** ノートの埋め込み（`![[別のノート]]`）、PDF などの埋め込み。埋め込みは取り除かれ、Actions の警告に出ます。

**本文中の `#タグ`：** タグとして扱われるのは、設定欄の `tags` だけです。本文の `#タグ` は、文字としてそのまま表示されます
（Obsidian のようなタグの表示や、リンクにはなりません）。Linter の「Move Tags to YAML」を使っていれば、本文のタグは自動で設定欄の `tags` に移るので、サイトのタグにもなります。

## Obsidian の Linter と一緒に使うとき

今の Linter の設定（`Insert YAML attributes`、`Move Tags to YAML`、`YAML Timestamp`）は、公開の仕組みとぶつかりません。

| Linter の動き | 公開への影響 |
|---|---|
| `date created` / `date modified` を書き換える | 無視します。**ノートの中身が同じで日時だけが変わったときは、記事を更新せず、再公開もしません。** |
| 空の `aliases:` と `tags:` を足す | 問題ありません（空の `tags` はタグなしとして扱います） |
| 本文の `#タグ` を設定欄の `tags` に移す | サイトのタグにもなります（上の「本文中の `#タグ`」を参照） |
| `tags` を複数行の形に整える | 問題ありません |

次の規則は、公開の項目とぶつかるので、有効にする前に注意してください。

| 規則 | 理由 |
|---|---|
| `YAML Title` / `YAML Title Alias` | `title` を見出しやファイル名で上書きします。記事のタイトルが変わります |
| `Remove YAML Keys` | 対象に `publish` や `slug` を入れると、公開の項目が消えます |
| `Paragraph Blank Lines` | 段落内の改行が空行に変わり、記事の改行の見え方が変わります |

**設定欄の書き方：** `title` に `:` や `#` が入るときは、`title: "A: B"` のように全体を引用符で囲んでください。
囲まないと設定欄が読み取れず、`publish: true` でも公開されません（Actions にエラーが出ます。同じ `slug` の公開済みの記事は消えません）。

## 公開を止める・直す・URL

- **直す：** ノートを直して push すると、次の同期で記事も直ります。公開日は変わりません。
  タイトル・本文・タグ・画像のどれも変わらなければ、記事は更新しません。
- **公開を止める：** `publish: true` を外す（またはノートを消す）と、次の同期で記事がサイトから消えます。
  GitHub の履歴には残ります。
- **slug を変える：** 別の記事として扱われ、**URL が変わります**（公開日は新しく決まります）。
  古い記事は消えます。公開した後は、slug を変えないでください。
- **一度に消えるのは2本まで：** 3本以上を一度に消そうとすると、事故を防ぐために同期を止めて知らせます。
  意図したものなら、`python scripts/obsidian_sync.py --vault vault --site . --allow-remove-many` を手元で実行してください。
- **WordPress から移した記事**は、この仕組みでは変更も削除もしません。

## すぐに公開したいとき
1時間待たずに、GitHub の **Actions → Sync Obsidian posts → Run workflow** を押すと、すぐに同期します。
スマートフォンのブラウザからも押せます。

## 最初に一度だけ必要な設定

Obsidian のリポジトリが**非公開**の場合は、読み取り専用の鍵（トークン）を登録します。
（画面の名前は変わることがあります）

1. GitHub の **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**。
2. 名前（例：`hugo-obsidian-read`）、有効期限（最長を推奨）を決めます。
3. **Repository access** は「Only select repositories」で `Obsidian` だけを選びます。
4. **Permissions → Repository permissions → Contents** を **Read-only** にします。
5. 作成したトークンをコピーし、**このリポジトリの Settings → Secrets and variables → Actions → New repository secret** に、
   名前 `OBSIDIAN_REPO_TOKEN` で登録します。
6. **Actions → Sync Obsidian posts → Run workflow** で、動作を確かめます。

トークンの有効期限が切れると、同期が失敗してお知らせが届きます。新しいトークンを作って、同じ名前で登録し直してください。

## 困ったとき

| 症状 | 確認すること |
|---|---|
| 1時間たっても公開されない | Actions の「Sync Obsidian posts」の実行を開く。Summary に、取り込んだ記事と、エラー・警告の一覧があります |
| 実行が `Repository not found` で失敗する | `OBSIDIAN_REPO_TOKEN` が未登録か、期限切れ、または権限が足りません |
| 「slug が未設定か不正です」 | `slug` を半角小文字・数字・ハイフンにする（最大6語） |
| 画像が表示されない | Summary の警告に、見つからない画像の名前が出ます。画像が Obsidian リポジトリにあるか確認してください |
| 同期が成功しているのに公開されない | 「Deploy Hugo site to Pages」が実行されているか、Actions で確認してください |

## 注意
- **このリポジトリは公開です。** 公開した記事（本文・画像）は、GitHub 上で誰でも読めます。下書きは Obsidian 側に置いてください。
- GitHub の仕様で、リポジトリに60日間まったく動きがないと、毎時の自動実行が止まることがあります。
  止まったときは、Actions の画面で有効にし直してください。
- スクリプトは `scripts/obsidian_sync.py` です。画像の取り込み記録は `scripts/obsidian_sync_state.json` にあります。
