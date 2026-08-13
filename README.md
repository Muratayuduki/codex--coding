# Codex personal configuration

このリポジトリは `~/.codex` に置き、Codexの個人設定を直接Git管理します。`AGENTS.md`、custom agents、hooks、user skills、実際にCodexが読む `config.toml` を編集すると、その変更がこのリポジトリの `git diff` に現れます。別ディレクトリとのコピー同期はありません。

## このPCでの配置

現在の作業コピーは `C:\vscode\codex-config`、Codexが実際に読むユーザー設定は `C:\Users\yuzup\.codex\config.toml` です。この作業コピーの `config.toml` は、Windows用のプロジェクト、Hook、通知、Marketplaceパスへ調整済みですが、ユーザー設定へ自動同期はしません。

Windows用Hookは `commandWindows` で `C:\vscode\codex-config\hooks` を参照し、Linux用の `command` も残しています。作業コピーを移動した場合は3つの `commandWindows` を更新し、Codexの `/hooks` で内容を確認して再承認してください。

`bootstrap.sh` はPOSIXのsymlinkを使うLinux・WSL向けです。Windows版Git Bashでは同じsymlinkを作れないため、このPCでは実行せず、作業コピーと実ユーザー設定を分けて管理します。

## 管理するファイル

- `AGENTS.md`: すべてのリポジトリに適用する個人指示
- `config.toml`: Codexが読む実設定
- `agents/`: custom agents
- `hooks/` と `git-hooks/`: Codex hooksとGitのpre-commit hook
- `user-skills/`: 個人用skillの実体
- `rules/`、`bootstrap.sh` とテスト

`.gitignore` はルート直下を原則として除外し、上記の設定だけを許可します。`auth.json`、`sessions/`、履歴、SQLite、ログ、cache、生成物、plugin cache、バックアップ、Codex同梱の `skills/.system/` は追跡しません。これらは別端末へ復元する対象にも含めません。

## User skills

Codexがユーザー単位のskillを読む公式の場所は `$HOME/.agents/skills` です。[公式資料](https://learn.chatgpt.com/docs/build-skills.md)には、この配置とsymlinkされたskillフォルダの読み込みが記載されています。

skillの実体はGit管理できるように `~/.codex/user-skills` へ置き、`bootstrap.sh` が `$HOME/.agents/skills` からsymlinkします。すでに別のファイル、ディレクトリ、またはsymlinkがある場合は、`~/.codex/backups/codex-bootstrap-<timestamp>-<process-id>/` へ退避してからリンクを作ります。リンク先が正しければ何も移動しないため、再実行しても同じ状態になります。

復元の正本は `user-skills/` の内容です。skill installerが端末ごとに作る `$HOME/.agents/.skill-lock.json` は、Git上のskill内容と一致するとは限らないため同期しません。skillを追加または更新した後は、`user-skills/` の差分を確認してコミットしてください。

## 新しい端末への復元

`~/.codex` がまだ存在しない端末では、private repositoryをその場所へcloneしてからbootstrapを実行します。

```bash
git clone https://github.com/sota411/codex-config.git "$HOME/.codex"
"$HOME/.codex/bootstrap.sh"
```

bootstrapは次の2点だけを設定します。

- `$HOME/.agents/skills` をリポジトリ内の `user-skills/` へsymlinkする
- global `core.hooksPath` をリポジトリ内の `git-hooks/` へ設定する

既存の `core.hooksPath` が別の値なら、bootstrapは上書きせず終了します。既存hookとの統合方法を決めてから、設定を手動で整理してください。

Codexの `/hooks` で、`config.toml` にある3つのHook commandと信頼状態を確認してください。`untrusted` なら内容を確認して承認すると、Codexが端末上の設定パスとcommandに対応する `[hooks.state]` を `config.toml` へ追記します。このprivate個人設定ではCodexが生成したstateも履歴に含めますが、`trusted_hash` を手作業やbootstrapで生成してはいけません。同じ絶対パスへcloneすると追跡済みstateが再利用されるため、Codexを初めて起動する前に `hooks/` と `config.toml` の差分を確認してください。別のパスへcloneした場合やHook commandを変更した場合は再承認し、Codexが更新したstateをコミットしてください。

Codex HookとGit pre-commitは、誤操作や秘密ファイルの混入を早めに止めるためのguardrailです。Git設定や実行環境を変更できる利用者に対するsecurity boundaryではないため、承認前の差分確認とprivate repositoryのアクセス管理は別途行ってください。

## 既存のCodex Homeから復元する場合

Codexを終了し、現在の `~/.codex` を必ずリポジトリ外へ退避してからcloneします。clone先が空でない状態では進めません。

```bash
backup_path="$HOME/.codex.backup-$(date '+%Y%m%d-%H%M%S')"
mv "$HOME/.codex" "$backup_path"
git clone https://github.com/sota411/codex-config.git "$HOME/.codex"
"$HOME/.codex/bootstrap.sh"
```

認証情報、session、履歴、DBなどは新しい `~/.codex` へコピーしません。Codexへのログインをやり直し、runtimeデータはCodexに再生成させます。退避先は復元後の動作を確認するまで削除しないでください。

旧 `~/codex-config` は自動同期に使いません。現在の移行を戻す必要がある場合のrollback用として、そのまま残しています。

## 日常の更新

設定は `~/.codex` 側で直接編集し、通常のGit操作で記録します。

```bash
git -C "$HOME/.codex" status --short
git -C "$HOME/.codex" diff
```

`config.toml` は実ファイルを追跡するため、端末固有のパスも差分になります。API key、OAuth token、cookieなどの秘密値は書かず、環境変数またはOSの秘密管理機能から渡してください。private repositoryでも秘密値はコミットしません。

このリポジトリでは `git clean -x` や `git clean -ffdx` を実行しないでください。Git管理外の認証情報、session、DB、cacheまで削除されます。cleanが必要なら、対象を限定したうえで先に `git clean -ndX` または `git clean -ndx` で削除候補を確認してください。

## 検証

```bash
bash "$HOME/.codex/tests/bootstrap_test.sh"
python3 -m unittest discover -s "$HOME/.codex/hooks" -p 'test_*.py'
python3 -m unittest discover -s "$HOME/.codex/tests" -p '*_test.py'
git -C "$HOME/.codex" diff --check
git -C "$HOME/.codex" diff --cached --check
```
