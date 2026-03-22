import asyncio
import os
import sys
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

load_dotenv()

app = FastAPI()
clients: list[WebSocket] = []


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


async def find_live_video_id(client: httpx.AsyncClient, api_key: str, channel_id: str) -> str:
    """チャンネルの現在配信中ライブのvideo_idを取得する"""
    resp = await client.get(
        "https://www.googleapis.com/youtube/v3/search",
        params={
            "part": "snippet",
            "channelId": channel_id,
            "type": "video",
            "eventType": "live",
            "key": api_key,
        },
    )
    resp.raise_for_status()
    data = resp.json()
    print(f"[デバッグ] Search APIレスポンス: {data}")
    items = data.get("items", [])
    if not items:
        return None
    return items[0]["id"]["videoId"]


async def get_live_chat_id(client: httpx.AsyncClient, api_key: str, video_id: str) -> str:
    resp = await client.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={"part": "liveStreamingDetails", "id": video_id, "key": api_key},
    )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    if not items:
        print(f"[エラー] 動画ID '{video_id}' が見つかりません", file=sys.stderr)
        return None
    chat_id = items[0].get("liveStreamingDetails", {}).get("activeLiveChatId")
    return chat_id


async def poll_youtube():
    api_key = os.getenv("YOUTUBE_API_KEY")
    channel_id = os.getenv("YOUTUBE_CHANNEL_ID")

    if not api_key or api_key == "YOUR_YOUTUBE_API_KEY_HERE":
        print("[エラー] .env の YOUTUBE_API_KEY を設定してください", file=sys.stderr)
        sys.exit(1)
    if not channel_id or channel_id == "YOUR_CHANNEL_ID_HERE":
        print("[エラー] .env の YOUTUBE_CHANNEL_ID を設定してください", file=sys.stderr)
        sys.exit(1)

    async with httpx.AsyncClient(timeout=10) as client:
        # ライブ配信開始を待つループ
        while True:
            print(f"[待機] チャンネル '{channel_id}' のライブ配信を検索中...")
            video_id = await find_live_video_id(client, api_key, channel_id)
            if video_id:
                print(f"[検出] ライブ配信を発見: video_id={video_id}")
                break
            print("[待機] ライブ配信が見つかりません。30秒後に再試行...")
            await asyncio.sleep(30)

        chat_id = await get_live_chat_id(client, api_key, video_id)
        if not chat_id:
            print("[エラー] ライブチャットIDを取得できませんでした", file=sys.stderr)
            sys.exit(1)
        print(f"[起動] ライブチャットID取得成功: {chat_id}")

        # 既存メッセージをスキップするため初回ページトークンだけ取得
        resp = await client.get(
            "https://www.googleapis.com/youtube/v3/liveChat/messages",
            params={
                "part": "snippet",
                "liveChatId": chat_id,
                "key": api_key,
                "maxResults": 200,
            },
        )
        data = resp.json()
        next_page_token = data.get("nextPageToken")
        poll_interval = data.get("pollingIntervalMillis", 5000) / 1000

        print(f"[起動] 新規コメント待機中... (ポーリング間隔: {poll_interval:.1f}秒)")

        while True:
            await asyncio.sleep(poll_interval)

            params = {
                "part": "snippet,authorDetails",
                "liveChatId": chat_id,
                "key": api_key,
                "maxResults": 200,
            }
            if next_page_token:
                params["pageToken"] = next_page_token

            try:
                resp = await client.get(
                    "https://www.googleapis.com/youtube/v3/liveChat/messages",
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"[警告] APIエラー: {e}")
                await asyncio.sleep(5)
                continue

            for item in data.get("items", []):
                msg_type = item["snippet"]["type"]
                author = item["authorDetails"]["displayName"]

                if msg_type == "textMessageEvent":
                    text = item["snippet"]["textMessageDetails"]["messageText"]
                    print(f"[コメント] {author}: {text}")
                    await broadcast({"type": "text", "author": author, "message": text})

                elif msg_type == "superChatEvent":
                    details = item["snippet"]["superChatDetails"]
                    amount = details.get("amountDisplayString", "")
                    text = details.get("userComment", "")
                    print(f"[スパチャ] {author} {amount}: {text}")
                    await broadcast({
                        "type": "superchat",
                        "author": author,
                        "message": text or f"{amount} のスーパーチャット！",
                        "amount": amount,
                    })

            next_page_token = data.get("nextPageToken", next_page_token)
            poll_interval = data.get("pollingIntervalMillis", 5000) / 1000


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
