import asyncio
import json
import os
import re
import sys

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

load_dotenv()

app = FastAPI()
clients: list[WebSocket] = []

# YouTube 内部APIエンドポイント（APIキー不要）
INNERTUBE_URL = "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Content-Type": "application/json",
}


async def broadcast(message: dict):
    disconnected = []
    for client in clients:
        try:
            await client.send_json(message)
        except Exception:
            disconnected.append(client)
    for c in disconnected:
        clients.remove(c)


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    clients.append(websocket)
    print(f"[接続] OBSブラウザソース接続 (接続数: {len(clients)})")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in clients:
            clients.remove(websocket)
        print(f"[切断] (接続数: {len(clients)})")


def extract_yt_initial_data(html: str) -> dict:
    """HTMLページから ytInitialData JSON を抽出する"""
    idx = html.find("ytInitialData")
    if idx == -1:
        return {}
    start = html.find("{", idx)
    if start == -1:
        return {}
    decoder = json.JSONDecoder()
    try:
        data, _ = decoder.raw_decode(html[start:])
        return data
    except json.JSONDecodeError:
        return {}


def extract_innertube_api_key(html: str) -> str:
    """HTMLページから innertubeApiKey を抽出する（なければ公開デフォルト値を使用）"""
    match = re.search(r'"INNERTUBE_API_KEY"\s*:\s*"([^"]+)"', html)
    if match:
        return match.group(1)
    return "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"


def extract_innertube_context(html: str) -> dict:
    """HTMLページから innertubeContext を抽出する"""
    match = re.search(r'"INNERTUBE_CONTEXT"\s*:\s*(\{.+?\})\s*,\s*"INNERTUBE', html, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    return {
        "client": {
            "clientName": "WEB",
            "clientVersion": "2.20240101.00.00",
            "hl": "ja",
            "gl": "JP",
        }
    }


def find_live_chat_continuation(data: dict) -> str | None:
    """ytInitialData からライブチャットの continuation トークンを取得する"""
    try:
        bar = data["contents"]["twoColumnWatchNextResults"]["conversationBar"]
        renderer = bar["liveChatRenderer"]
        for cont in renderer.get("continuations", []):
            for key in ("reloadContinuationData", "invalidationContinuationData", "timedContinuationData"):
                token = cont.get(key, {}).get("continuation")
                if token:
                    return token
    except (KeyError, TypeError):
        pass
    return None


def find_video_id(html: str) -> str | None:
    """HTMLから video_id を抽出する"""
    match = re.search(r'"videoId"\s*:\s*"([a-zA-Z0-9_-]{11})"', html)
    return match.group(1) if match else None


async def fetch_live_page(
    client: httpx.AsyncClient, channel_id: str
) -> tuple[str | None, str | None, str, dict]:
    """
    チャンネルのライブページを取得して
    (video_id, continuation, innertube_api_key, innertube_context) を返す。
    YOUTUBE_CHANNEL_ID は UC... 形式または @ハンドル 形式に対応。
    """
    if channel_id.startswith("UC"):
        url = f"https://www.youtube.com/channel/{channel_id}/live"
    elif channel_id.startswith("@"):
        url = f"https://www.youtube.com/{channel_id}/live"
    else:
        url = f"https://www.youtube.com/@{channel_id}/live"

    resp = await client.get(url, headers=HEADERS, follow_redirects=True)
    if resp.status_code != 200:
        return None, None, "", {}

    html = resp.text
    video_id = find_video_id(html)
    yt_data = extract_yt_initial_data(html)
    continuation = find_live_chat_continuation(yt_data)
    api_key = extract_innertube_api_key(html)
    context = extract_innertube_context(html)

    return video_id, continuation, api_key, context


def runs_to_text(runs: list) -> str:
    """message.runs からテキストを結合する（絵文字は短縮形で表示）"""
    parts = []
    for run in runs:
        if "text" in run:
            parts.append(run["text"])
        elif "emoji" in run:
            emoji = run["emoji"]
            shortcuts = emoji.get("shortcuts")
            if shortcuts:
                parts.append(shortcuts[0])
    return "".join(parts)


def parse_actions(actions: list) -> list[dict]:
    """actions リストからブロードキャスト用メッセージを生成する"""
    messages = []
    for action in actions:
        item = action.get("addChatItemAction", {}).get("item", {})

        if "liveChatTextMessageRenderer" in item:
            r = item["liveChatTextMessageRenderer"]
            author = r.get("authorName", {}).get("simpleText", "")
            text = runs_to_text(r.get("message", {}).get("runs", []))
            if author and text:
                messages.append({"type": "text", "author": author, "message": text})

        elif "liveChatPaidMessageRenderer" in item:
            r = item["liveChatPaidMessageRenderer"]
            author = r.get("authorName", {}).get("simpleText", "")
            text = runs_to_text(r.get("message", {}).get("runs", []))
            amount = r.get("purchaseAmountText", {}).get("simpleText", "")
            if author:
                messages.append({
                    "type": "superchat",
                    "author": author,
                    "message": text or f"{amount} のスーパーチャット！",
                    "amount": amount,
                })

        elif "liveChatPaidStickerRenderer" in item:
            r = item["liveChatPaidStickerRenderer"]
            author = r.get("authorName", {}).get("simpleText", "")
            amount = r.get("purchaseAmountText", {}).get("simpleText", "")
            if author:
                messages.append({
                    "type": "superchat",
                    "author": author,
                    "message": f"{amount} のスーパーステッカー！",
                    "amount": amount,
                })

    return messages


def extract_next_continuation(data: dict) -> tuple[str | None, float]:
    """レスポンスから次の continuation トークンとポーリング間隔(秒)を取得する"""
    chat_cont = (
        data.get("continuationContents", {})
        .get("liveChatContinuation", {})
    )
    for cont in chat_cont.get("continuations", []):
        for key in ("timedContinuationData", "invalidationContinuationData", "liveChatReplayContinuationData"):
            if key in cont:
                token = cont[key].get("continuation")
                timeout_ms = cont[key].get("timeoutMs", 5000)
                return token, timeout_ms / 1000
    return None, 5.0


async def poll_live_chat(
    client: httpx.AsyncClient,
    continuation: str,
    api_key: str,
    context: dict,
    skip_first: bool = True,
) -> None:
    """
    continuation トークンを使ってライブチャットをポーリングし続ける。
    skip_first=True のとき最初のバッチは表示せずにスキップする（既存メッセージを無視）。
    ライブ終了で continuation が取れなくなったら return する。
    """
    poll_interval = 5.0
    first = skip_first

    while continuation:
        if not first:
            await asyncio.sleep(poll_interval)

        payload = {"continuation": continuation, "context": context}
        try:
            resp = await client.post(
                f"{INNERTUBE_URL}?key={api_key}",
                json=payload,
                headers=HEADERS,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[警告] チャット取得エラー: {e}")
            await asyncio.sleep(5)
            continue

        chat_cont = data.get("continuationContents", {}).get("liveChatContinuation", {})
        actions = chat_cont.get("actions", [])

        if not first:
            for msg in parse_actions(actions):
                if msg["type"] == "text":
                    print(f"[コメント] {msg['author']}: {msg['message']}")
                else:
                    print(f"[スパチャ] {msg['author']} {msg.get('amount', '')}: {msg['message']}")
                await broadcast(msg)

        first = False
        continuation, poll_interval = extract_next_continuation(data)

    print("[終了] ライブチャットの continuation が切れました（配信終了？）")


async def poll_youtube():
    channel_id = os.getenv("YOUTUBE_CHANNEL_ID")
    if not channel_id or channel_id == "YOUR_CHANNEL_ID_HERE":
        print("[エラー] .env の YOUTUBE_CHANNEL_ID を設定してください", file=sys.stderr)
        sys.exit(1)

    async with httpx.AsyncClient(timeout=15) as client:
        while True:
            # ライブ配信が始まるまで待機
            while True:
                print(f"[待機] チャンネル '{channel_id}' のライブ配信を検索中...")
                video_id, continuation, api_key, context = await fetch_live_page(client, channel_id)
                if continuation:
                    print(f"[検出] ライブ配信を発見: video_id={video_id}")
                    break
                print("[待機] ライブ配信が見つかりません。30秒後に再試行...")
                await asyncio.sleep(30)

            print("[起動] 既存コメントをスキップして新規コメント待機中...")
            await poll_live_chat(client, continuation, api_key, context, skip_first=True)

            print("[再起動] 30秒後にライブ配信を再検索...")
            await asyncio.sleep(30)


@app.on_event("startup")
async def startup():
    asyncio.create_task(poll_youtube())


app.mount("/", StaticFiles(directory="static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    print(f"[起動] サーバー起動: http://localhost:{port}")
    print(f"[OBS] ブラウザソースURL: http://localhost:{port}/overlay.html")
    uvicorn.run(app, host="0.0.0.0", port=port)
