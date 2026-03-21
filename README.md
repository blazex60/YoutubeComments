# YouTube Comments Overlay for OBS

YouTubeのライブ配信コメントをリアルタイムにOBSへオーバーレイ表示するシステムです。

## 機能

- チャンネルIDからライブ配信を自動検出（配信前に起動しておける）
- LINEライクな吹き出しUIで右端に表示
- 新コメントが下から追加され古いものが上に流れる
- スーパーチャットをゴールド吹き出しで強調表示

## セットアップ

### 1. 依存パッケージのインストール

```bash
uv venv
uv pip install -r requirements.txt
```

### 2. YouTube Data API v3 キーの取得

1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクトを作成
2. 「APIとサービス」→「ライブラリ」から **YouTube Data API v3** を有効化
3. 「認証情報」→「APIキーを作成」

### 3. 環境変数の設定

`.env.example` をコピーして `.env` を作成し、値を入力します。

```bash
cp .env.example .env
```

```env
YOUTUBE_API_KEY=取得したAPIキー
YOUTUBE_CHANNEL_ID=UCxxxxxxxxxxxxxxxxxx  # チャンネルIDはUCから始まる
PORT=8080
MAX_COMMENTS=8
```

**チャンネルIDの確認方法:**
YouTubeのチャンネルページURLから取得します。
`https://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxx` の `UC〜` 部分がチャンネルIDです。

### 4. サーバー起動

```bash
uv run python server.py
```

配信前に起動しておくと、ライブ開始を自動で検出してコメント取得を始めます。

### 5. OBSの設定

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

## ファイル構成

```
.
├── server.py          # メインサーバー（YouTube APIポーリング + WebSocket）
├── static/
│   ├── overlay.html   # OBSブラウザソース用UI
│   └── style.css      # 吹き出しスタイル
├── .env               # APIキー・設定（Git管理外）
├── .env.example       # 設定テンプレート
└── requirements.txt   # 依存パッケージ
```
