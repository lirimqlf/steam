import asyncio
"""
discord_notify.py — Discord webhook notifications (replaces bot.py/Telegram)
Set DISCORD_WEBHOOK_URL in _env to enable.
"""
import aiohttp
from datetime import datetime


class DiscordNotifier:
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    async def _post(self, payload: dict):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.webhook_url, json=payload) as r:
                    if r.status not in (200, 204):
                        text = await r.text()
                        print(f"[!] Discord webhook {r.status}: {text}")
        except Exception as e:
            print(f"[!] Discord webhook error: {e}")

    async def notify_new_dead(self, name, discord_id, steam_appid, current_players,
                              total_reviews=0, inactive_days=None, score=0, contact=""):
        dev_url   = f"https://discord.com/developers/applications/{discord_id}"
        steam_url = f"https://store.steampowered.com/app/{steam_appid}"
        inactive  = f"{inactive_days}d" if inactive_days is not None else "jamais"
        await self._post({
            "embeds": [{
                "title":       f"💀 {name}  ·  score {score}/100",
                "description": f"**[Claim on Discord]({dev_url})** · [Steam]({steam_url})",
                "color":       0xFF4444,
                "fields": [
                    {"name": "Players now", "value": f"`{current_players}`", "inline": True},
                    {"name": "Reviews",     "value": f"`{total_reviews}`",   "inline": True},
                    {"name": "Dernière activité", "value": f"`{inactive}`",  "inline": True},
                    {"name": "Contact",     "value": contact or "aucun",     "inline": False},
                ],
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "footer": {"text": "Dead Game Checker"},
            }]
        })

    async def notify_batch(self, items: list):
        """Alertes groupées : 10 embeds par message, avec pause (évite le 429 de Discord)."""
        for i in range(0, len(items), 10):
            embeds = []
            for it in items[i:i + 10]:
                ina = it.get("inactive_days")
                embeds.append({
                    "title": f"{it['name']}  ·  score {it['score']}/100", "color": 0xFFFFFF,
                    "description": f"**[Claim on Discord](https://discord.com/developers/applications/{it['discord_id']})** · [Steam](https://store.steampowered.com/app/{it['steam_appid']})",
                    "fields": [{"name": "Reviews", "value": f"`{it['total_reviews']}`", "inline": True},
                               {"name": "Inactive", "value": f"`{str(ina) + 'd' if ina is not None else 'never'}`", "inline": True},
                               {"name": "Contact", "value": it.get("contact") or "none", "inline": True}]})
            await self._post({"content": f"{len(items)} new dead games, best scores first" if i == 0 else "", "embeds": embeds})
            await asyncio.sleep(1.5)

    async def notify_scan_complete(self, total: int, dead: int, new_dead: int):
        await self._post({
            "embeds": [{
                "title": "✅ Scan complete",
                "color": 0x00E676,
                "fields": [
                    {"name": "Processed", "value": f"`{total}`",    "inline": True},
                    {"name": "Dead",      "value": f"`{dead}`",     "inline": True},
                    {"name": "New dead",  "value": f"`{new_dead}`", "inline": True},
                ],
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "footer": {"text": "Dead Game Checker"},
            }]
        })
