---
name: "ux-monkey-test"
description: "WebアプリのUXと画面遷移をモンキーテストで検証する。フロントエンド/UI変更後の動作確認、画面遷移の網羅確認、ランダム操作によるエラー炙り出し、UXレビューが必要なとき、または「モンキーテスト」「monkey test」「画面遷移確認」「UX確認」と指示されたときに使用する。Playwrightで系統的クロールとシード付きランダム操作を実行し、エラー一覧・遷移マップ・スクリーンショット・UX所見のレポートを生成する。"
compatibility: "Node.js 18+ と npx が必要。初回実行時に scripts/ で npm install と chromium ダウンロードを行う。対象はHTTP(S)で到達可能なWebアプリ。"
---

# UXモンキーテスト

WebアプリのUXと画面遷移を、Playwright による系統的クロールとシード付きランダム操作で検証する。

## 実行フロー

1. **入力確認**: 対象URLを特定する。ユーザー指示・直前の作業文脈（dev serverのポート等）から判断できない場合はユーザーに質問する。ログインが必要なサイトの場合は Playwright `storageState` JSON のパスを受け取る。
2. **前提確認**（初回のみ）:
   - `node --version` を実行し、Node.js 18+ であることを確認する。条件を満たさない場合は中断してインストール手順を提示する。
   - `npx --version` を実行し、`npx` が存在することを確認する。存在しない場合は中断してインストール手順を提示する。
   - `~/.agents/skills/ux-monkey-test/scripts/node_modules` が無ければ `npm install --prefix ~/.agents/skills/ux-monkey-test/scripts` を実行する。
   - `npx --prefix ~/.agents/skills/ux-monkey-test/scripts playwright install chromium` を実行してブラウザを取得する。
3. **出力ディレクトリ作成**: 対象プロジェクトのcwd直下に `./monkey-test-report/<YYYYMMDD-HHmmss>/` を作成する。`.gitignore` への追記はユーザーの指示がある場合のみ行う。
4. **フェーズ1 — 系統的クロール**: `node ~/.agents/skills/ux-monkey-test/scripts/crawl.mjs --url <URL> --out <outdir> [オプション]` を実行する。
5. **フェーズ2 — ランダムモンキー**: `node ~/.agents/skills/ux-monkey-test/scripts/monkey.mjs --url <URL> --out <outdir> [オプション]` を実行する。クロール結果の画面数に応じて `--events` を調整する。目安は画面数×50、最低300とする。
6. **フェーズ3 — レポート生成**: `<outdir>/report.md` を下記の構成で作成する。スクリーンショットは実際に画像を開いて見た上でUX所見を書く。
7. **報告**: `report.md` の要約として、エラー件数・デッドエンド有無・主要UX所見をユーザーに提示する。

## オプション

`crawl.mjs`:

- `--url` 必須。起点URL。
- `--out` 必須。出力ディレクトリ。
- `--max-pages` 任意。探索する最大画面数。既定値は `30`。
- `--viewport` 任意。`desktop`（1280x800）または `mobile`（390x844）。既定値は `desktop`。
- `--storage-state` 任意。ログイン済み Playwright storageState JSON のパス。

`monkey.mjs`:

- `--url` 必須。起点URL。
- `--out` 必須。出力ディレクトリ。
- `--events` 任意。実行するランダム操作数。既定値は `300`。
- `--seed` 任意。乱数シード。既定値は `42`。
- `--viewport` 任意。`desktop` または `mobile`。既定値は `desktop`。
- `--storage-state` 任意。ログイン済み Playwright storageState JSON のパス。

## 出力

`crawl.mjs` は `<outdir>/transitions.json` と `<outdir>/screenshots/screen-*.png` を作成する。

`transitions.json` の構造:

```json
{
  "screens": [{ "id": "screen-1", "url": "http://localhost:3000/", "title": "Home", "screenshot": "screenshots/screen-1.png", "reachedFrom": [] }],
  "edges": [{ "from": "screen-1", "to": "screen-2", "trigger": "link: About" }],
  "unreachedLinks": [],
  "consoleErrors": []
}
```

`monkey.mjs` は `<outdir>/actions.jsonl`、`<outdir>/errors.json`、`<outdir>/screenshots/error-*.png` を作成し、終了時に統計を JSON で stdout に出力する。

## report.md の構成

1. **サマリ**: 対象URL・実行日時・画面数・遷移数・エラー件数・再現情報（seed / events / viewport）
2. **エラー一覧**: 各エラーの種別・メッセージ・発生URL・直前操作履歴・再現コマンド（`node monkey.mjs --url ... --seed ... --events ...`）・スクリーンショットへの相対リンク
3. **画面遷移マップ**: Mermaid `graph TD` 図。デッドエンド（出エッジがなく戻る手段もない画面）と未到達リンクを明記する
4. **スクリーンショット一覧**: 各画面の画像への相対リンク
5. **UX所見**: 画像を実際に見た上での気づき（タップ領域の小ささ、遷移の分かりにくさ、レイアウト崩れ、コントラスト不足等）。根拠となるスクリーンショットを必ず併記する

## 制約・注意

- スクリプトが非0終了した場合はエラーを隠蔽せず、そのまま出力を提示して中断する。
- 対象が本番環境と思われる場合（localhost / 127.0.0.1 / プライベートIP以外）はユーザーに確認してから実行する。
- 破壊的操作（フォームsubmitによるデータ作成等）が起こり得ることを実行前にユーザーへ告知する。
- 同一オリジン外、`mailto:`、`tel:`、ダウンロードリンクは辿らず、`unreachedLinks` に記録する。
- `monkey.mjs` は `Math.random` を使わず、シード付き擬似乱数で操作を選ぶ。
