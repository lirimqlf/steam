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

    async def notify_new_dead(
        self,
        name: str,
        discord_id: str,
        steam_appid: str,
        current_players: int,
        players_2weeks: int,
        owners: str,
    ):
        dev_url   = f"https://discord.com/developers/applications/{discord_id}"
        steam_url = f"https://store.steampowered.com/app/{steam_appid}"

        await self._post({
            "embeds": [{
                "title":       f"💀 {name}",
                "description": f"**[Claim on Discord]({dev_url})** · [Steam]({steam_url})",
                "color":       0xFF4444,
                "fields": [
                    {"name": "Players now", "value": f"`{current_players}`", "inline": True},
                    {"name": "Players 2w",  "value": f"`{players_2weeks}`",  "inline": True},
                    {"name": "Owners",      "value": owners or "N/A",        "inline": True},
                ],
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "footer": {"text": "Dead Game Checker"},
            }]
        })

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
