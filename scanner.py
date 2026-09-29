"""
scanner.py — async scanner with DB persistence and live broadcast
"""
import asyncio
import aiohttp
from datetime import datetime
from typing import Optional, Callable, Awaitable

import database as db

DEAD_THRESHOLD_CURRENT  = 0
DEAD_THRESHOLD_2WEEKS   = 10
CONCURRENCY_STEAM       = 10
CONCURRENCY_STEAMSPY    = 3
STEAMSPY_DELAY          = 1.1

DISCORD_DETECTABLE_URL  = "https://discord.com/api/v10/applications/detectable"
STEAM_PLAYER_COUNT_URL  = "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"
STEAMSPY_URL            = "https://steamspy.com/api.php"


class Scanner:
    def __init__(self, broadcast_fn: Callable[[dict], Awaitable[None]] = None):
        self.is_running   = False
        self._stop_flag   = False
        self._broadcast   = broadcast_fn or (lambda d: asyncio.sleep(0))

        # Live progress state
        self.scan_id:         Optional[int] = None
        self.progress:        int  = 0
        self.total:           int  = 0
        self.current_game:    str  = ""
        self.dead_count:      int  = 0
        self.new_dead_count:  int  = 0
        self.started_at:      Optional[str] = None

    def get_status(self) -> dict:
        return {
            "is_running":    self.is_running,
            "scan_id":       self.scan_id,
            "progress":      self.progress,
            "total":         self.total,
            "current_game":  self.current_game,
            "dead_count":    self.dead_count,
            "new_dead_count":self.new_dead_count,
            "started_at":    self.started_at,
            "pct":           round(self.progress / self.total * 100) if self.total else 0,
        }

    def stop(self):
        self._stop_flag = True

    async def _broadcast_progress(self):
        await self._broadcast({"type": "scan_progress", **self.get_status()})

    # ── Fetch helpers ────────────────────────────────────────────────────────

    async def _fetch_discord_games(self, session: aiohttp.ClientSession) -> list[dict]:
        async with session.get(DISCORD_DETECTABLE_URL, headers={"User-Agent": "Mozilla/5.0"}) as r:
            if r.status != 200:
                return []
            return await r.json()

    @staticmethod
    def _extract_steam_appid(game: dict) -> Optional[str]:
        for sku in game.get("third_party_skus", []):
            if sku.get("distributor") == "steam":
                return sku.get("id")
        return None

    async def _fetch_steam_players(self, session: aiohttp.ClientSession, appid: str) -> Optional[int]:
        try:
            async with session.get(STEAM_PLAYER_COUNT_URL, params={"appid": appid}, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    return None
                data = await r.json()
                return data.get("response", {}).get("player_count")
        except Exception:
            return None

    async def _fetch_steamspy(self, session: aiohttp.ClientSession, appid: str) -> dict:
        try:
            async with session.get(STEAMSPY_URL, params={"request": "appdetails", "appid": appid}, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200:
                    return {}
                return await r.json(content_type=None)
        except Exception:
            return {}

    # ── Process one game ─────────────────────────────────────────────────────

    async def _process_game(
        self,
        session:    aiohttp.ClientSession,
        game_raw:   dict,
        steam_sem:  asyncio.Semaphore,
        spy_sem:    asyncio.Semaphore,
        tg_bot,
    ):
        if self._stop_flag:
            return

        appid = self._extract_steam_appid(game_raw)
        if not appid:
            return

        name       = game_raw.get("name", "Unknown")
        discord_id = game_raw.get("id", "")

        # Fetch in parallel
        async with steam_sem:
            current_players = await self._fetch_steam_players(session, appid)

        async with spy_sem:
            spy = await self._fetch_steamspy(session, appid)
            await asyncio.sleep(STEAMSPY_DELAY)

        players_2weeks = spy.get("players_2weeks")
        owners         = spy.get("owners")
        avg_forever    = spy.get("average_forever", 0) or 0

        # Determine status
        is_dead = (
            (current_players is not None and current_players <= DEAD_THRESHOLD_CURRENT) and
            (players_2weeks  is None or players_2weeks <= DEAD_THRESHOLD_2WEEKS)
        )
        status = "dead" if is_dead else "alive"

        # Check if it's a NEW dead game
        existing = None
        async with db.get_db() as conn:
            cursor = await conn.execute(
                "SELECT status FROM games WHERE discord_id = ?", [discord_id]
            )
            row = await cursor.fetchone()
            existing = dict(row)["status"] if row else None

        is_new_dead = is_dead and (existing is None or existing != "dead")

        await db.upsert_game({
            "discord_id":      discord_id,
            "name":            name,
            "steam_appid":     appid,
            "icon":            game_raw.get("icon"),
            "current_players": current_players,
            "players_2weeks":  players_2weeks,
            "owners":          owners,
            "avg_forever":     avg_forever,
            "status":          status,
            "last_checked":    datetime.utcnow().isoformat(),
        })

        # Update live counters
        self.progress    += 1
        self.current_game = name
        if is_dead:
            self.dead_count += 1
        if is_new_dead:
            self.new_dead_count += 1

        await self._broadcast_progress()

        # Telegram notification for new dead games
        if is_new_dead and tg_bot:
            await tg_bot.notify_new_dead(
                name=name,
                discord_id=discord_id,
                steam_appid=appid,
                current_players=current_players or 0,
                players_2weeks=players_2weeks or 0,
                owners=owners or "N/A",
            )

    # ── Main run ─────────────────────────────────────────────────────────────

    async def run(self, tg_bot=None):
        if self.is_running:
            return

        self.is_running      = True
        self._stop_flag      = False
        self.progress        = 0
        self.total           = 0
        self.dead_count      = 0
        self.new_dead_count  = 0
        self.started_at      = datetime.utcnow().isoformat()
        self.current_game    = ""

        self.scan_id = await db.create_scan()
        await self._broadcast({"type": "scan_started", "scan_id": self.scan_id})

        try:
            connector = aiohttp.TCPConnector(limit=50)
            async with aiohttp.ClientSession(connector=connector) as session:

                # 1. Fetch Discord game list
                self.current_game = "Fetching Discord games..."
                await self._broadcast_progress()
                discord_games = await self._fetch_discord_games(session)

                steam_games = [g for g in discord_games if self._extract_steam_appid(g)]
                self.total = len(steam_games)

                await db.update_scan(self.scan_id, total_games=self.total)
                await self._broadcast_progress()

                # 2. Process all games concurrently
                steam_sem = asyncio.Semaphore(CONCURRENCY_STEAM)
                spy_sem   = asyncio.Semaphore(CONCURRENCY_STEAMSPY)

                tasks = [
                    self._process_game(session, g, steam_sem, spy_sem, tg_bot)
                    for g in steam_games
                ]
                await asyncio.gather(*tasks, return_exceptions=True)

            # 3. Finalize scan
            finish_status = "stopped" if self._stop_flag else "completed"
            await db.update_scan(
                self.scan_id,
                finished_at     = datetime.utcnow().isoformat(),
                total_processed = self.progress,
                total_dead      = self.dead_count,
                new_dead        = self.new_dead_count,
                status          = finish_status,
            )

            await self._broadcast({
                "type":           "scan_complete",
                "scan_id":        self.scan_id,
                "total_processed":self.progress,
                "total_dead":     self.dead_count,
                "new_dead":       self.new_dead_count,
                "status":         finish_status,
            })

            # Telegram summary
            if tg_bot:
                await tg_bot.notify_scan_complete(
                    total=self.progress,
                    dead=self.dead_count,
                    new_dead=self.new_dead_count,
                )

        except Exception as e:
            await db.update_scan(self.scan_id, status="failed", finished_at=datetime.utcnow().isoformat())
            await self._broadcast({"type": "scan_error", "error": str(e)})
        finally:
            self.is_running = False
