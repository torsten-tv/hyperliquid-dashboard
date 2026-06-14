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
WINDOW = "month"          # rank traders by 30-day PnL
TOP_N = 20
FILL_LOOKBACK_DAYS = 14   # how far back to pull fills (entry times + change windows)
MAX_WORKERS = 8
HEADERS = {"Content-Type": "application/json", "User-Agent": "hl-dashboard/1.0"}

OUT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "docs", "data", "snapshot.json",
)


def _post(body: dict, retries: int = 3):
    for attempt in range(retries):
        try:
            r = requests.post(INFO_URL, json=body, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
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


def build_trader(row: dict, now_ms: int) -> dict | None:
    addr = row["ethAddress"]
    try:
        state = _post({"type": "clearinghouseState", "user": addr})
        start = now_ms - FILL_LOOKBACK_DAYS * 86_400_000
        fills = _post({"type": "userFillsByTime", "user": addr,
                       "startTime": start, "aggregateByTime": True}) or []
    except Exception as exc:  # noqa: BLE001
        print(f"  ! skip {addr}: {exc}", file=sys.stderr)
        return None

    # newest "open" fill per perp coin -> entry time
    entry_time: dict[str, int] = {}
    for f in sorted(fills, key=lambda x: x["time"]):
        coin = f["coin"]
        if coin.startswith("@"):  # spot pair id, not a perp
            continue
        kind = classify_dir(f.get("dir", ""))
        if kind in ("open", "flip"):
            entry_time[coin] = f["time"]

    positions = []
    for ap in state.get("assetPositions", []):
        p = ap["position"]
        szi = float(p["szi"])
        if szi == 0:
            continue
        coin = p["coin"]
        lev = p.get("leverage", {})
        positions.append({
            "coin": coin,
            "side": "long" if szi > 0 else "short",
            "szi": abs(szi),
            "entryPx": float(p["entryPx"]) if p.get("entryPx") else None,
            "notionalUsd": float(p.get("positionValue", 0) or 0),
            "leverage": f"{lev.get('value', '?')}x {lev.get('type', '')}".strip(),
            "liqPx": float(p["liquidationPx"]) if p.get("liquidationPx") else None,
            "uPnl": float(p.get("unrealizedPnl", 0) or 0),
            "entryTime": entry_time.get(coin),
        })

    def changes_for(window_ms: int) -> dict:
        """Aggregate fills per coin within the window (HFT accounts have
        thousands of micro-fills; raw events are noise). Returns
        {coin: {openedSz, closedSz, n, lastTime}}."""
        cutoff = now_ms - window_ms
        per: dict[str, dict] = {}
        for f in fills:
            if f["time"] < cutoff or f["coin"].startswith("@"):
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

    perf = window_perf(row, WINDOW)
    return {
        "addr": addr,
        "name": row.get("displayName") or None,
        "accountValue": float(row.get("accountValue", 0) or 0),
        "monthPnl": perf["pnl"],
        "monthRoi": perf["roi"],
        "positions": positions,
        "changes": {"h1": changes_for(3_600_000), "h4": changes_for(4 * 3_600_000)},
    }


def main() -> int:
    now_ms = int(time.time() * 1000)
    print("Fetching leaderboard...")
    rows = fetch_leaderboard()
    rows.sort(key=lambda r: window_perf(r, WINDOW)["pnl"], reverse=True)
    top = rows[:TOP_N]
    print(f"Top {len(top)} by {WINDOW} PnL. Fetching positions + fills...")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        traders = [t for t in pool.map(lambda r: build_trader(r, now_ms), top)
                   if t is not None]

    # aggregate long/short counts per coin across displayed positions
    agg: dict[str, dict] = {}
    for t in traders:
        for p in t["positions"]:
            a = agg.setdefault(p["coin"], {"coin": p["coin"], "long": 0, "short": 0})
            a[p["side"]] += 1
    aggregates = sorted(agg.values(),
                        key=lambda a: a["long"] + a["short"], reverse=True)

    snapshot = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generatedAtMs": now_ms,
        "window": WINDOW,
        "topN": TOP_N,
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
