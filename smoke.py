"""python smoke.py 50  : scanne N jeux pour de vrai et affiche ce qui marche (a lancer en local)."""
import asyncio, sys, aiohttp, database as db
from scanner import Scanner, DISCORD_DETECTABLE_URL


async def main(n):
    await db.init_db()
    sc = Scanner(); sc.cfg = await db.get_criteria(); sc.sc = await db.get_scanner_cfg(); sc.known = {}
    async with aiohttp.ClientSession() as s:
        async with s.get(DISCORD_DETECTABLE_URL) as r:
            games = await r.json()
        games = [g for g in games if sc._extract_steam_appid(g)][:n]
        print(f"{len(games)} jeux Steam sur la liste Discord")
        ss, ds = asyncio.Semaphore(8), asyncio.Semaphore(1)
        await asyncio.gather(*[sc._process_game(s, g, ss, ds, None) for g in games])
    async with db.get_db() as c:
        cur = await c.execute("SELECT status, COUNT(*) n, SUM(total_reviews IS NOT NULL) avec_reviews FROM games GROUP BY status")
        for r in await cur.fetchall():
            print(dict(r))
    print("echecs reseau :", sc.failed)

asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 50))
