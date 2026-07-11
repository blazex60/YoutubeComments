# YouTube Comments Overlay for OBS

YouTubeのライブ配信コメントをリアルタイムにOBSへオーバーレイ表示するシステムです。

## 機能

- チャンネルIDからライブ配信を自動検出（配信前に起動しておける）
- LINEライクな吹き出しUIで右端に表示
- 新コメントが下から追加され古いものが上に流れる
- スーパーチャットを金額ティアに応じた色の吹き出しで強調表示
- スーパーステッカー、メンバーシップ加入、ギフトメンバーシップ購入も表示
- OBSのカラーソース（背景色）を検知し、ライト/ダークテーマを自動切り替え
- URLパラメータで吹き出し色・文字色などを個別にカスタマイズ可能

## セットアップ

### 1. 依存パッケージのインストール

```bash
uv venv
uv pip install -r requirements.txt
```

### 2. 環境変数の設定

`.env.example` をコピーして `.env` を作成し、値を入力します。

```bash
cp .env.example .env
```

```env
YOUTUBE_CHANNEL_ID=UCxxxxxxxxxxxxxxxxxx  # UCから始まるチャンネルID、または @ハンドル 形式
PORT=8080

# OBS WebSocket連携（任意）
OBS_WS_HOST=localhost
OBS_WS_PORT=4455
OBS_WS_PASSWORD=
```

> **APIキー不要！**
> YouTube Data API v3 の代わりに YouTube の内部APIを直接使用するため、
> Google Cloud Console の設定や課金は一切不要です。

**チャンネルIDの確認方法:**
- `UC...` 形式: チャンネルページURL `https://www.youtube.com/channel/UCxxxxxxxx` の `UC〜` 部分
- `@ハンドル` 形式: `@channelname` をそのまま指定も可能

**OBS WebSocket連携について:**
OBSの「ツール」→「WebSocket サーバー設定」で有効化し、ホスト・ポート・パスワードを
`.env` に設定すると、現在のシーン内のカラーソースの背景色を検知して、
オーバーレイの吹き出し配色を背景の明るさに応じて自動でライト/ダーク切り替えします。
使わない場合はこれらの変数を未設定のままにしておけば無視されます。

### 3. サーバー起動

```bash
uv run python server.py
```

配信前に起動しておくと、ライブ開始を自動で検出してコメント取得を始めます。

### 4. OBSの設定

1. ソース追加 → **「ブラウザ」**
2. 以下を設定:

| 項目 | 値 |
|------|-----|
| URL | `http://localhost:8080/overlay.html` |
| 幅 | `360` |
| 高さ | `900`（配信解像度に合わせて調整） |

3. カスタムCSS欄に以下を貼り付け:

```css
body {
  background-color: rgba(0, 0, 0, 0) !important;
  margin: 0;
  overflow: hidden;
}
```

4. ブラウザソースを配信シーンの右端に配置

**表示件数の変更:**
最大表示コメント数は `static/overlay.html` 内の `MAX_COMMENTS` 定数（デフォルト8件）を
編集して変更します。

**URLパラメータでの見た目カスタマイズ（任意）:**

| パラメータ | 説明 |
|-----------|------|
| `?bg=%23rrggbb` | 背景色から輝度を判定してテーマを自動切り替え |
| `?bubbleBg=...` | 吹き出しの背景色を直接指定 |
| `?textColor=...` | 本文の文字色を直接指定 |
| `?authorColor=...` | 投稿者名の文字色を直接指定 |

## ファイル構成

```
.
├── server.py          # メインサーバー（YouTube内部APIポーリング + WebSocket配信 + OBS連携）
├── static/
│   ├── overlay.html   # OBSブラウザソース用UI
│   └── style.css      # 吹き出しスタイル
├── .env               # チャンネルID・設定（Git管理外）
├── .env.example       # 設定テンプレート
└── requirements.txt   # 依存パッケージ
```
