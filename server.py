import asyncio
import base64
import hashlib
import json
import os
import re
import sys

import httpx
import websockets
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


def argb_to_hex(argb: int) -> str:
    """ARGB整数値を #rrggbb 形式のHEX文字列に変換する"""
    r = (argb >> 16) & 0xFF
    g = (argb >> 8) & 0xFF
    b = argb & 0xFF
    return f"#{r:02x}{g:02x}{b:02x}"


def runs_to_plain(runs: list) -> str:
    """runs リストをプレーンテキストに変換する（絵文字なし）"""
    return "".join(run.get("text", "") for run in runs if "text" in run)


def parse_actions(actions: list) -> list[dict]:
    """actions リストからブロードキャスト用メッセージを生成する"""
    messages = []
    for action in actions:
        item = action.get("addChatItemAction", {}).get("item", {})

        # 通常コメント
        if "liveChatTextMessageRenderer" in item:
            r = item["liveChatTextMessageRenderer"]
            author = r.get("authorName", {}).get("simpleText", "")
            text = runs_to_text(r.get("message", {}).get("runs", []))
            avatar = _extract_avatar(r)
            if author and text:
                messages.append({
                    "type": "text",
                    "author": author,
                    "message": text,
                    "avatar": avatar,
                })

        # スーパーチャット
        elif "liveChatPaidMessageRenderer" in item:
            r = item["liveChatPaidMessageRenderer"]
            author = r.get("authorName", {}).get("simpleText", "")
            text = runs_to_text(r.get("message", {}).get("runs", []))
            amount = r.get("purchaseAmountText", {}).get("simpleText", "")
            avatar = _extract_avatar(r)
            header_color = argb_to_hex(r["headerBackgroundColor"]) if "headerBackgroundColor" in r else None
            body_color = argb_to_hex(r["bodyBackgroundColor"]) if "bodyBackgroundColor" in r else None
            if author:
                messages.append({
                    "type": "superchat",
                    "author": author,
                    "message": text or f"{amount} のスーパーチャット！",
                    "amount": amount,
                    "avatar": avatar,
                    "headerColor": header_color,
                    "bodyColor": body_color,
                })

        # スーパーステッカー
        elif "liveChatPaidStickerRenderer" in item:
            r = item["liveChatPaidStickerRenderer"]
            author = r.get("authorName", {}).get("simpleText", "")
            amount = r.get("purchaseAmountText", {}).get("simpleText", "")
            avatar = _extract_avatar(r)
            header_color = argb_to_hex(r["backgroundColor"]) if "backgroundColor" in r else None
            if author:
                messages.append({
                    "type": "sticker",
                    "author": author,
                    "message": f"スーパーステッカー！",
                    "amount": amount,
                    "avatar": avatar,
                    "headerColor": header_color,
                    "bodyColor": header_color,
                })

        # メンバーシップ加入
        elif "liveChatMembershipItemRenderer" in item:
            r = item["liveChatMembershipItemRenderer"]
            author = r.get("authorName", {}).get("simpleText", "")
            avatar = _extract_avatar(r)
            header_text = runs_to_plain(r.get("headerPrimaryText", {}).get("runs", []))
            sub_text = runs_to_plain(r.get("headerSubtext", {}).get("runs", []))
            if author:
                messages.append({
                    "type": "membership",
                    "author": author,
                    "message": header_text or "メンバーになりました！",
                    "subtext": sub_text,
                    "avatar": avatar,
                })

        # ギフトメンバーシップ購入
        elif "liveChatSponsorshipsGiftPurchaseAnnouncementRenderer" in item:
            r = item["liveChatSponsorshipsGiftPurchaseAnnouncementRenderer"]
            author = r.get("authorName", {}).get("simpleText", "")
            avatar = _extract_avatar(r)
            header = r.get("header", {}).get("liveChatSponsorshipsHeaderRenderer", {})
            primary = runs_to_plain(header.get("primaryText", {}).get("runs", []))
            if author:
                messages.append({
                    "type": "gift",
                    "author": author,
                    "message": primary or "メンバーシップをギフトしました！",
                    "avatar": avatar,
                })

    return messages


def _extract_avatar(renderer: dict) -> str | None:
    """レンダラーからアバター画像URLを取得する"""
    try:
        thumbnails = renderer["authorPhoto"]["thumbnails"]
        if thumbnails:
            return thumbnails[-1]["url"]
    except (KeyError, IndexError, TypeError):
        pass
    return None


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
                timeout_ms = cont[key].get("timeoutMs", 2000)
                # 最大2秒に制限してリアルタイム性を向上
                return token, min(timeout_ms / 1000, 2.0)
    return None, 2.0


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
    poll_interval = 2.0
    first = skip_first

    while continuation:
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
            messages = parse_actions(actions)
            for msg in messages:
                if msg["type"] == "text":
                    print(f"[コメント] {msg['author']}: {msg['message']}")
                else:
                    print(f"[スパチャ] {msg['author']} {msg.get('amount', '')}: {msg['message']}")
                await broadcast(msg)
            # メッセージがあった場合は即座に再フェッチ、なければ待機
            if messages:
                poll_interval = 0.5
            else:
                _, poll_interval = extract_next_continuation(data)
        else:
            _, poll_interval = extract_next_continuation(data)

        first = False
        continuation, _ = extract_next_continuation(data)
        await asyncio.sleep(poll_interval)

    print("[終了] ライブチャットの continuation が切れました（配信終了？）")


async def _obs_request(ws, request_type: str, request_data: dict | None = None) -> dict:
    """obs-websocket v5 のリクエスト（op=6）を送り、レスポンス（op=7）を返す"""
    payload = {
        "op": 6,
        "d": {
            "requestType": request_type,
            "requestId": request_type,
            "requestData": request_data or {},
        },
    }
    await ws.send(json.dumps(payload))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("op") == 7 and msg["d"].get("requestId") == request_type:
            return msg["d"].get("responseData", {})


async def get_obs_bg_color(ws) -> str | None:
    """現在シーンのカラーソース色を取得して #rrggbb 文字列を返す。なければ None"""
    try:
        scene_data = await _obs_request(ws, "GetCurrentProgramScene")
        scene_name = scene_data.get("currentProgramSceneName")
        if not scene_name:
            return None

        items_data = await _obs_request(ws, "GetSceneItemList", {"sceneName": scene_name})
        for item in items_data.get("sceneItems", []):
            if item.get("inputKind") in ("color_source_v3", "color_source"):
                source_name = item.get("sourceName")
                if not source_name:
                    continue
                settings = await _obs_request(ws, "GetInputSettings", {"inputName": source_name})
                color_argb = settings.get("inputSettings", {}).get("color")
                if color_argb is not None:
                    return argb_to_hex(int(color_argb))
    except Exception as e:
        print(f"[OBS WS] 色取得エラー: {e}")
    return None


async def poll_obs_bg() -> None:
    """OBS WebSocketに接続してカラーソースの色を監視し、変化時にブロードキャストする"""
    host = os.getenv("OBS_WS_HOST", "localhost")
    port = os.getenv("OBS_WS_PORT", "4455")
    password = os.getenv("OBS_WS_PASSWORD", "")
    uri = f"ws://{host}:{port}"
    last_color: str | None = None

    while True:
        try:
            async with websockets.connect(uri) as ws:
                # Hello (op=0)
                hello = json.loads(await ws.recv())
                auth_d: dict = {"rpcVersion": 1}

                # パスワード認証が必要な場合
                if password and "authentication" in hello.get("d", {}):
                    auth_info = hello["d"]["authentication"]
                    secret = base64.b64encode(
                        hashlib.sha256((password + auth_info["salt"]).encode()).digest()
                    ).decode()
                    auth_str = base64.b64encode(
                        hashlib.sha256((secret + auth_info["challenge"]).encode()).digest()
                    ).decode()
                    auth_d["authentication"] = auth_str

                # Identify (op=1)
                await ws.send(json.dumps({"op": 1, "d": auth_d}))
                # Identified (op=2)
                await ws.recv()
                print(f"[OBS WS] 接続完了 ({uri})")

                while True:
                    color = await get_obs_bg_color(ws)
                    if color and color != last_color:
                        last_color = color
                        print(f"[OBS WS] 背景色更新: {color}")
                        await broadcast({"type": "bg_color", "color": color})
                    await asyncio.sleep(3)

        except Exception as e:
            print(f"[OBS WS] 接続エラー: {e} — 5秒後に再接続")
            await asyncio.sleep(5)


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
    asyncio.create_task(poll_obs_bg())


app.mount("/", StaticFiles(directory="static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    print(f"[起動] サーバー起動: http://localhost:{port}")
    print(f"[OBS] ブラウザソースURL: http://localhost:{port}/overlay.html")
    uvicorn.run(app, host="0.0.0.0", port=port)
