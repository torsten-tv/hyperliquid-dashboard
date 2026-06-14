"""Fetch Hyperliquid top-trader data and write docs/data/snapshot.json.

Runs in GitHub Actions (cron) and locally. No secrets needed - all public data.

Output schema: see README.md.
"""
from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
INFO_URL = "https://api.hyperliquid.xyz/info"
WINDOW = "month"          # rank traders by 30-day window
TOP_N = 20
CANDIDATES = 80           # scan this deep per metric; the biggest accounts are often
                          # flat, so we over-fetch (cheap clearinghouseState) and keep
                          # only active ones. Fills (expensive) run for selected only.
MIN_ACCOUNT = 10_000_000  # only "whale" accounts (>=$10M) — see README
# coin whitelist: only these perps are tracked; everything else is dropped
COINS = ["BTC", "ETH", "ATOM", "TAO", "XRP", "SOL", "BNB", "DOGE", "AVAX"]
COINSET = set(COINS)
FOCUS = COINS             # score tiles = the whole tracked universe
FILL_LOOKBACK_DAYS = 14   # how far back to pull fills (entry times + change windows)
MAX_WORKERS = 5
HEADERS = {"Content-Type": "application/json", "User-Agent": "hl-dashboard/1.0"}

OUT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "docs", "data", "snapshot.json",
)


def _post(body: dict, retries: int = 5):
    for attempt in range(retries):
        try:
            r = requests.post(INFO_URL, json=body, headers=HEADERS, timeout=30)
            if r.status_code == 429:          # rate limited -> longer backoff
                time.sleep(3 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception:  # noqa: BLE001
            if attempt == retries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
    return None


def fetch_leaderboard() -> list[dict]:
    r = requests.get(LEADERBOARD_URL, headers=HEADERS, timeout=60)
    r.raise_for_status()
    return r.json()["leaderboardRows"]


def window_perf(row: dict, window: str) -> dict:
    for name, perf in row.get("windowPerformances", []):
        if name == window:
            return {"pnl": float(perf["pnl"]), "roi": float(perf["roi"]),
                    "vlm": float(perf["vlm"])}
    return {"pnl": 0.0, "roi": 0.0, "vlm": 0.0}


def classify_dir(d: str) -> str | None:
    """Map a fill 'dir' string to 'open' / 'close' / 'flip' / None (spot)."""
    if d.startswith("Open"):
        return "open"
    if d.startswith("Close") or d.startswith("Liquidated"):
        return "close"
    if ">" in d:  # "Long > Short" / "Short > Long" position flip
        return "flip"
    return None  # spot Buy/Sell etc. - ignore for perp position tracking


def fetch_positions(row: dict) -> dict | None:
    """Phase 1 (cheap): clearinghouseState only -> trader with open positions.
    entryTime + 1h/4h changes are filled later for selected traders only."""
    addr = row["ethAddress"]
    try:
        state = _post({"type": "clearinghouseState", "user": addr})
    except Exception as exc:  # noqa: BLE001
        print(f"  ! skip {addr}: {exc}", file=sys.stderr)
        return None

    positions = []
    for ap in state.get("assetPositions", []):
        p = ap["position"]
        szi = float(p["szi"])
        if szi == 0 or p["coin"] not in COINSET:   # only whitelisted coins
            continue
        lev = p.get("leverage", {})
        positions.append({
            "coin": p["coin"],
            "side": "long" if szi > 0 else "short",
            "szi": abs(szi),
            "entryPx": float(p["entryPx"]) if p.get("entryPx") else None,
            "notionalUsd": float(p.get("positionValue", 0) or 0),
            "leverage": f"{lev.get('value', '?')}x {lev.get('type', '')}".strip(),
            "liqPx": float(p["liquidationPx"]) if p.get("liquidationPx") else None,
            "uPnl": float(p.get("unrealizedPnl", 0) or 0),
            "entryTime": None,
        })

    perf = window_perf(row, WINDOW)
    return {
        "addr": addr,
        "name": row.get("displayName") or None,
        "accountValue": float(row.get("accountValue", 0) or 0),
        "monthPnl": perf["pnl"],
        "monthRoi": perf["roi"],
        "positions": positions,
    }


def enrich_fills(t: dict, now_ms: int) -> dict:
    """Phase 2: add entry times + 1h/4h change aggregates from userFillsByTime."""
    addr = t["addr"]
    try:
        start = now_ms - FILL_LOOKBACK_DAYS * 86_400_000
        fills = _post({"type": "userFillsByTime", "user": addr,
                       "startTime": start, "aggregateByTime": True}) or []
    except Exception as exc:  # noqa: BLE001
        print(f"  ! fills failed {addr}: {exc}", file=sys.stderr)
        fills = []

    # newest "open" fill per perp coin -> entry time
    entry_time: dict[str, int] = {}
    for f in sorted(fills, key=lambda x: x["time"]):
        coin = f["coin"]
        if coin.startswith("@"):  # spot pair id, not a perp
            continue
        if classify_dir(f.get("dir", "")) in ("open", "flip"):
            entry_time[coin] = f["time"]
    for p in t["positions"]:
        p["entryTime"] = entry_time.get(p["coin"])

    def changes_for(window_ms: int) -> dict:
        """Aggregate fills per coin within the window (HFT accounts have
        thousands of micro-fills; raw events are noise)."""
        cutoff = now_ms - window_ms
        per: dict[str, dict] = {}
        for f in fills:
            if f["time"] < cutoff or f["coin"] not in COINSET:
                continue
            kind = classify_dir(f.get("dir", ""))
            if kind is None:
                continue
            sz = float(f["sz"])
            c = per.setdefault(f["coin"], {"openedSz": 0.0, "closedSz": 0.0,
                                           "n": 0, "lastTime": 0})
            if kind in ("open", "flip"):
                c["openedSz"] += sz
            if kind in ("close", "flip"):
                c["closedSz"] += sz
            c["n"] += 1
            c["lastTime"] = max(c["lastTime"], f["time"])
        return per

    t["changes"] = {"h1": changes_for(3_600_000), "h4": changes_for(4 * 3_600_000)}
    return t


def main() -> int:
    now_ms = int(time.time() * 1000)
    print("Fetching leaderboard...")
    rows = fetch_leaderboard()
    elig = [r for r in rows
            if float(r.get("accountValue", 0) or 0) >= MIN_ACCOUNT]
    # candidate set goes deeper than TOP_N: the biggest accounts are often flat
    # (no open perp position) and we only keep traders that actually hold positions.
    cand_pnl = sorted(elig, key=lambda r: window_perf(r, WINDOW)["pnl"], reverse=True)[:CANDIDATES]
    cand_roi = sorted(elig, key=lambda r: window_perf(r, WINDOW)["roi"], reverse=True)[:CANDIDATES]
    seen, cand = set(), []
    for r in cand_pnl + cand_roi:
        if r["ethAddress"] not in seen:
            seen.add(r["ethAddress"])
            cand.append(r)
    print(f"{len(elig)} accounts >= ${MIN_ACCOUNT:,}. Phase 1: positions of "
          f"{len(cand)} candidates (top {CANDIDATES} by PnL + ROI)...")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        fetched = [t for t in pool.map(fetch_positions, cand) if t is not None]

    # keep only accounts that actually hold open positions, then rank among those
    active = [t for t in fetched if t["positions"]]
    active_by_pnl = sorted(active, key=lambda t: t["monthPnl"], reverse=True)
    active_by_roi = sorted(active, key=lambda t: t["monthRoi"], reverse=True)
    pnl_rank = {t["addr"]: i + 1 for i, t in enumerate(active_by_pnl)}
    roi_rank = {t["addr"]: i + 1 for i, t in enumerate(active_by_roi)}

    seen, traders = set(), []
    for t in active_by_pnl[:TOP_N] + active_by_roi[:TOP_N]:
        if t["addr"] not in seen:
            seen.add(t["addr"])
            traders.append(t)
    for t in traders:
        t["rankPnl"] = pnl_rank.get(t["addr"])
        t["rankRoi"] = roi_rank.get(t["addr"])
    print(f"{len(active)}/{len(fetched)} candidates hold positions. "
          f"Phase 2: fills for {len(traders)} selected (top {TOP_N} by PnL + ROI)...")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        list(pool.map(lambda t: enrich_fills(t, now_ms), traders))

    # aggregate per coin: counts + notional-weighted Smart-Money-Score (-100..+100)
    agg: dict[str, dict] = {}
    for t in traders:
        for p in t["positions"]:
            a = agg.setdefault(p["coin"], {"coin": p["coin"], "long": 0, "short": 0,
                                           "longNotional": 0.0, "shortNotional": 0.0})
            a[p["side"]] += 1
            a["longNotional" if p["side"] == "long" else "shortNotional"] += p["notionalUsd"]
    for a in agg.values():
        tot = a["longNotional"] + a["shortNotional"]
        a["totalNotional"] = tot
        # +100 = all notional long, -100 = all short, 0 = balanced/none
        a["score"] = round((a["longNotional"] - a["shortNotional"]) / tot * 100) if tot else 0
    for coin in FOCUS:  # focus coins always present, even with no position
        agg.setdefault(coin, {"coin": coin, "long": 0, "short": 0, "longNotional": 0.0,
                              "shortNotional": 0.0, "totalNotional": 0.0, "score": 0})
    aggregates = sorted(agg.values(), key=lambda a: a["totalNotional"], reverse=True)

    snapshot = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generatedAtMs": now_ms,
        "window": WINDOW,
        "topN": TOP_N,
        "minAccount": MIN_ACCOUNT,
        "focus": FOCUS,
        "traders": traders,
        "aggregates": aggregates,
    }

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, ensure_ascii=False, indent=1)
    n_pos = sum(len(t["positions"]) for t in traders)
    print(f"Wrote {OUT_PATH}: {len(traders)} traders, {n_pos} positions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
