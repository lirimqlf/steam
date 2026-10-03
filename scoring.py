"""scoring.py — critères du score, activables/pondérables. Source unique (scanner, API, Excel)."""
import time

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


def merge(saved: dict) -> dict:
    out = {}
    for k, label, pts in CRITERIA:
        v = (saved or {}).get(k) or {}
        try:
            p = max(0, min(100, int(v.get("points", pts))))
        except (TypeError, ValueError):
            p = pts
        out[k] = {"label": label, "enabled": bool(v.get("enabled", True)), "points": p}
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
        "self_pub": 1.0 if devs & pubs else 0.0,
        "inactive": 1.0 if (ina is None or ina >= 730) else (0.5 if ina >= 365 else 0.0),
        "no_reviews": 1.0 if row.get("total_reviews") == 0 else 0.0,
        "cheap": 1.0 if (row.get("is_free") or (price is not None and price <= 500)) else 0.0,
        "early_abandoned": 1.0 if (row.get("early_access") and (ina is None or ina >= 365)) else 0.0,
        "streak": min(row.get("zero_streak") or 0, 5) / 5,
    }


def breakdown(row: dict, cfg: dict) -> dict:
    """Points gagnés par critère ACTIVÉ uniquement."""
    f = _fractions(row)
    return {k: round(cfg[k]["points"] * f[k], 1) for k in cfg if cfg[k]["enabled"]}


def compute(row: dict, cfg: dict) -> int:
    """Score normalisé 0-100 sur le total des critères activés."""
    mx = sum(c["points"] for c in cfg.values() if c["enabled"])
    return round(100 * sum(breakdown(row, cfg).values()) / mx) if mx else 0
