"""scoring.py — critères du score, activables/pondérables. Source unique (scanner, API, Excel)."""
import time, re

CRITERIA = [  # (clé, label, points par défaut)
    ("email",           "Email de contact",                               30),
    ("website",         "Site / page support",                            10),
    ("self_pub",        "Dev = éditeur (il décide seul)",                 20),
    ("inactive",        "Inactif longtemps (≥2 ans = 100 %, ≥1 an = 50 %)", 15),
    ("no_reviews",      "0 review",                                       10),
    ("cheap",           "Gratuit ou ≤ 5 $",                               10),
    ("early_abandoned", "Early Access abandonné",                         10),
    ("streak",          "0 joueur sur plusieurs scans (max 5)",            5),
]
LABELS = {k: l for k, l, _ in CRITERIA}


FIELDS = {"total_reviews": "Reviews", "current_players": "Players now", "price": "Price ($)",
          "inactive_days": "Days inactive", "zero_streak": "Scans at 0 players",
          "release_year": "Release year", "is_free": "Free (1 or 0)", "early_access": "Early Access (1 or 0)"}
OPS = (">=", "<=", "==")


def merge(saved: dict) -> dict:
    out = {}
    for k, label, pts in CRITERIA:
        v = (saved or {}).get(k) or {}
        try:
            p = max(0, min(100, int(v.get("points", pts))))
        except (TypeError, ValueError):
            p = pts
        out[k] = {"label": label, "enabled": bool(v.get("enabled", True)), "points": p}
    for k, v in (saved or {}).items():          # règles ajoutées par l'utilisateur
        if not (k.startswith("custom_") and isinstance(v, dict)):
            continue
        try:
            fld, op, val = v.get("field"), v.get("op"), float(v.get("value"))
            p = max(0, min(100, int(v.get("points", 0))))
        except (TypeError, ValueError):
            continue
        if fld in FIELDS and op in OPS:
            out[k[:40]] = {"label": (v.get("label") or f"{FIELDS[fld]} {op} {val:g}")[:80],
                           "enabled": bool(v.get("enabled", True)), "points": p,
                           "custom": True, "field": fld, "op": op, "value": val}
    return out


def inactive_days(row: dict):
    acts = [t for t in (row.get("last_review_ts"), row.get("last_news_ts")) if t]
    return int((time.time() - max(acts)) // 86400) if acts else None


def _fractions(row: dict) -> dict:
    c = row.get("contact") or ""
    email = row.get("email") or (c if "@" in c else "")
    site = row.get("website") or (c if c and "@" not in c else "")
    devs = {x.strip().lower() for x in (row.get("developers") or "").split(",") if x.strip()}
    pubs = {x.strip().lower() for x in (row.get("publishers") or "").split(",") if x.strip()}
    ina = inactive_days(row)
    price = row.get("price")
    return {
        "email": 1.0 if email else 0.0,
        "website": 1.0 if site else 0.0,
        "self_pub": 1.0 if (row.get("self_pub") if row.get("self_pub") is not None else devs & pubs) else 0.0,
        "inactive": 1.0 if (ina is None or ina >= 730) else (0.5 if ina >= 365 else 0.0),
        "no_reviews": 1.0 if row.get("total_reviews") == 0 else 0.0,
        "cheap": 1.0 if (row.get("is_free") or (price is not None and price <= 500)) else 0.0,
        "early_abandoned": 1.0 if (row.get("early_access") and (ina is None or ina >= 365)) else 0.0,
        "streak": min(row.get("zero_streak") or 0, 5) / 5,
    }


def _val(row: dict, f: str):
    if f == "inactive_days":
        return inactive_days(row)
    if f == "price":
        p = row.get("price")
        return 0 if row.get("is_free") else (None if p is None else p / 100)
    if f == "release_year":
        m = re.search(r"(19|20)\d{2}", row.get("release_date") or "")
        return int(m.group()) if m else None
    v = row.get(f)
    return None if v is None else float(v)


def _rule(row: dict, c: dict) -> float:
    v = _val(row, c["field"])
    if v is None:
        return 0.0
    ok = v >= c["value"] if c["op"] == ">=" else v <= c["value"] if c["op"] == "<=" else v == c["value"]
    return 1.0 if ok else 0.0


def breakdown(row: dict, cfg: dict) -> dict:
    """Points gagnés par critère ACTIVÉ (intégrés + règles perso)."""
    f = _fractions(row)
    return {k: round(cfg[k]["points"] * (_rule(row, cfg[k]) if cfg[k].get("custom") else f[k]), 1)
            for k in cfg if cfg[k]["enabled"]}


def compute(row: dict, cfg: dict) -> int:
    """Score normalisé 0-100 sur le total des critères activés."""
    mx = sum(c["points"] for c in cfg.values() if c["enabled"])
    return round(100 * sum(breakdown(row, cfg).values()) / mx) if mx else 0
