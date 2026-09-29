"""
server.py — FastAPI backend
"""
import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler

load_dotenv(dotenv_path="_env")

import database as db
from scanner import Scanner

# ── WebSocket broadcast ───────────────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.connections:
            self.connections.remove(ws)

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.connections:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.connections.remove(ws)


manager   = ConnectionManager()
scanner   = Scanner(broadcast_fn=manager.broadcast)
notifier  = None
scheduler = AsyncIOScheduler(timezone="UTC")

# ── Scheduled scan ────────────────────────────────────────────────────────────

async def scheduled_scan():
    if not scanner.is_running:
        print(f"[⏰] Scheduled scan triggered at {datetime.utcnow().isoformat()}")
        asyncio.create_task(scanner.run(notifier))

# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global notifier

    await db.init_db()

    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if webhook_url:
        from discord_notify import DiscordNotifier
        notifier = DiscordNotifier(webhook_url)
        print("[+] Discord webhook notifier ready")
    else:
        print("[~] No DISCORD_WEBHOOK_URL set — notifications disabled")

    interval_hours = int(os.getenv("SCAN_INTERVAL_HOURS", "0"))
    if interval_hours > 0:
        scheduler.add_job(scheduled_scan, "interval", hours=interval_hours, id="auto_scan")
        scheduler.start()
        print(f"[⏰] Auto-scan every {interval_hours}h")

    yield

    if scheduler.running:
        scheduler.shutdown(wait=False)

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Discord Dead Game Checker", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API ───────────────────────────────────────────────────────────────────────

@app.get("/api/stats")
async def get_stats():
    return await db.get_stats()


@app.get("/api/games")
async def get_games(
    status:   Optional[str] = None,
    search:   Optional[str] = None,
    sort:     str = "current",
    page:     int = 1,
    per_page: int = 50,
):
    return await db.get_games(
        status=status, search=search,
        sort=sort, page=page, per_page=per_page,
    )


@app.get("/api/scans")
async def get_scans():
    return await db.get_scan_history()


@app.get("/api/scan/status")
async def scan_status():
    return scanner.get_status()


@app.post("/api/scan/start")
async def start_scan(background_tasks: BackgroundTasks):
    if scanner.is_running:
        raise HTTPException(409, "A scan is already running")
    background_tasks.add_task(scanner.run, notifier)
    return {"ok": True}


@app.post("/api/scan/stop")
async def stop_scan():
    if not scanner.is_running:
        raise HTTPException(409, "No scan running")
    scanner.stop()
    return {"ok": True}


@app.get("/api/notifier/status")
async def notifier_status():
    return {"connected": notifier is not None}


class PatchGame(BaseModel):
    status: str
    notes:  Optional[str] = None


@app.patch("/api/games/{discord_id}/status")
async def patch_status(discord_id: str, body: PatchGame):
    valid = {"dead", "alive", "unchecked"}
    if body.status not in valid:
        raise HTTPException(400, f"status must be one of {valid}")
    await db.patch_game_status(discord_id, body.status, body.notes)
    await manager.broadcast({
        "type": "game_updated",
        "discord_id": discord_id,
        "status": body.status,
    })
    return {"ok": True}


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    await websocket.send_json({"type": "scan_status", **scanner.get_status()})
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ── Static ────────────────────────────────────────────────────────────────────

Path("static").mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    return FileResponse("static/index.html")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
