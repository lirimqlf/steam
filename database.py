"""
database.py — SQLite schema + helpers via aiosqlite
"""
import aiosqlite
import os
import json
from datetime import datetime
from typing import Optional
from contextlib import asynccontextmanager

# On Render set DB_PATH=/data/games.db (persistent disk mount)
DB_PATH = os.getenv("DB_PATH", "games.db")


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")

        await db.execute("""
        CREATE TABLE IF NOT EXISTS games (
            discord_id      TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            steam_appid     TEXT,
            icon            TEXT,
            current_players INTEGER,
            players_2weeks  INTEGER,
            owners          TEXT,
            avg_forever     INTEGER,
            status          TEXT DEFAULT 'unchecked',
            last_checked    TEXT,
            first_seen      TEXT DEFAULT (datetime('now')),
            notes           TEXT
        )""")

        await db.execute("""
        CREATE TABLE IF NOT EXISTS scans (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at      TEXT DEFAULT (datetime('now')),
            finished_at     TEXT,
            total_games     INTEGER DEFAULT 0,
            total_processed INTEGER DEFAULT 0,
            total_dead      INTEGER DEFAULT 0,
            new_dead        INTEGER DEFAULT 0,
            status          TEXT DEFAULT 'running'
        )""")

        await db.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            game_discord_id TEXT,
            event_type      TEXT,
            sent_at         TEXT DEFAULT (datetime('now')),
            webhook_msg_id  TEXT
        )""")

        await db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_games_status ON games(status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_games_steam  ON games(steam_appid)")
        # migration auto des nouvelles colonnes (DB existante OK)
        for col, typ in [("total_reviews","INTEGER"),("last_review_ts","INTEGER"),("last_news_ts","INTEGER"),
                         ("app_type","TEXT"),("release_date","TEXT"),("developers","TEXT"),("publishers","TEXT"),
                         ("contact","TEXT"),("email","TEXT"),("website","TEXT"),("is_free","INTEGER"),("price","INTEGER"),("early_access","INTEGER"),
                         ("zero_streak","INTEGER DEFAULT 0"),("score","INTEGER DEFAULT 0")]:
            try:
                await db.execute(f"ALTER TABLE games ADD COLUMN {col} {typ}")
            except Exception:
                pass
        await db.execute("CREATE INDEX IF NOT EXISTS idx_games_score ON games(score)")
        await db.commit()


@asynccontextmanager
async def get_db():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        yield db


async def get_stats() -> dict:
    async with get_db() as db:
        cursor = await db.execute("SELECT COUNT(*) FROM games")
        total  = (await cursor.fetchone())[0]

        cursor = await db.execute("SELECT COUNT(*) FROM games WHERE status='dead'")
        dead   = (await cursor.fetchone())[0]

        cursor = await db.execute("SELECT COUNT(*) FROM games WHERE status='alive'")
        alive  = (await cursor.fetchone())[0]

        cursor = await db.execute("""
            SELECT COUNT(*) FROM games
            WHERE status='dead' AND last_checked >= datetime('now', '-1 day')
        """)
        new_dead = (await cursor.fetchone())[0]

        cursor = await db.execute("""
            SELECT id, started_at, finished_at, status, total_processed, total_games, total_dead
            FROM scans ORDER BY id DESC LIMIT 1
        """)
        last_scan = await cursor.fetchone()

        return {
            "total":        total,
            "dead":         dead,
            "alive":        alive,
            "new_dead_24h": new_dead,
            "last_scan":    dict(last_scan) if last_scan else None,
        }


async def get_games(
    status:   Optional[str] = None,
    search:   Optional[str] = None,
    sort:     str = "score",
    page:     int = 1,
    per_page: int = 50,
    min_score:   int = 0,
    has_contact: bool = False,
) -> dict:
    conditions, params = [], []
    if status and status != "all":
        conditions.append("status = ?"); params.append(status)
    else:
        conditions.append("status != 'ignored'")
    if search:
        conditions.append("name LIKE ?"); params.append(f"%{search}%")
    if min_score:
        conditions.append("COALESCE(score,0) >= ?"); params.append(min_score)
    if has_contact:
        conditions.append("contact IS NOT NULL AND contact != ''")
    where = "WHERE " + " AND ".join(conditions)

    sort_map = {
        "score":        "COALESCE(score,0) DESC, COALESCE(total_reviews,999999) ASC",
        "current":      "COALESCE(current_players, 999999) ASC",
        "name":         "name ASC",
        "last_checked": "last_checked DESC",
    }
    order  = sort_map.get(sort, sort_map["score"])
    offset = (page - 1) * per_page

    async with get_db() as db:
        cursor = await db.execute(f"SELECT COUNT(*) FROM games {where}", params)
        total  = (await cursor.fetchone())[0]
        cursor = await db.execute(f"""
            SELECT discord_id, name, steam_appid, icon, current_players, total_reviews, score,
                   contact, release_date, developers, publishers, zero_streak,
                   last_review_ts, last_news_ts, status, last_checked, notes
            FROM games {where}
            ORDER BY {order}
            LIMIT ? OFFSET ?
        """, params + [per_page, offset])
        games = [dict(r) for r in await cursor.fetchall()]
        return {"games": games, "total": total, "page": page, "per_page": per_page}


async def get_scan_history(limit: int = 20) -> list:
    async with get_db() as db:
        cursor = await db.execute("""
            SELECT id, started_at, finished_at, total_games, total_processed,
                   total_dead, new_dead, status
            FROM scans ORDER BY id DESC LIMIT ?
        """, [limit])
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def upsert_game(game: dict):
    cols = list(game.keys())
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "discord_id")
    async with get_db() as db:
        await db.execute(f"""
            INSERT INTO games ({", ".join(cols)}) VALUES ({", ".join(":" + c for c in cols)})
            ON CONFLICT(discord_id) DO UPDATE SET {updates}
        """, game)
        await db.commit()


async def create_scan() -> int:
    async with get_db() as db:
        cursor = await db.execute(
            "INSERT INTO scans (status) VALUES ('running') RETURNING id"
        )
        row = await cursor.fetchone()
        await db.commit()
        return row[0]


async def update_scan(scan_id: int, **kwargs):
    if not kwargs:
        return
    sets   = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [scan_id]
    async with get_db() as db:
        await db.execute(f"UPDATE scans SET {sets} WHERE id = ?", values)
        await db.commit()


async def patch_game_status(discord_id: str, status: str, notes: Optional[str] = None):
    async with get_db() as db:
        if notes is not None:
            await db.execute(
                "UPDATE games SET status=?, notes=? WHERE discord_id=?",
                [status, notes, discord_id],
            )
        else:
            await db.execute(
                "UPDATE games SET status=? WHERE discord_id=?",
                [status, discord_id],
            )
        await db.commit()


# ── Critères de score + export ───────────────────────────────────────────────
async def get_criteria() -> dict:
    import scoring
    async with get_db() as db:
        cur = await db.execute("SELECT value FROM settings WHERE key='criteria'")
        row = await cur.fetchone()
    return scoring.merge(json.loads(row[0]) if row else {})


async def set_criteria(body: dict) -> dict:
    import scoring
    cfg = scoring.merge(body)
    slim = {k: {"enabled": v["enabled"], "points": v["points"]} for k, v in cfg.items()}
    async with get_db() as db:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES ('criteria', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", [json.dumps(slim)])
        await db.commit()
    return cfg


async def recompute_scores() -> int:
    import scoring
    cfg = await get_criteria()
    async with get_db() as db:
        cur = await db.execute("SELECT * FROM games WHERE status IN ('dead','dying')")
        rows = [dict(r) for r in await cur.fetchall()]
        await db.executemany("UPDATE games SET score=? WHERE discord_id=?",
                             [(scoring.compute(r, cfg), r["discord_id"]) for r in rows])
        await db.commit()
    return len(rows)


async def export_rows(statuses: list, min_score: int = 0, has_contact: bool = False) -> list:
    q = f"SELECT * FROM games WHERE status IN ({','.join('?' * len(statuses))}) AND COALESCE(score,0) >= ?"
    if has_contact:
        q += " AND contact IS NOT NULL AND contact != ''"
    q += " ORDER BY COALESCE(score,0) DESC, COALESCE(total_reviews,999999) ASC"
    async with get_db() as db:
        cur = await db.execute(q, [*statuses, min_score])
        return [dict(r) for r in await cur.fetchall()]
