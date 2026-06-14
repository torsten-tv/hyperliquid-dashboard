# Hyperliquid Top-Trader Dashboard

Ein statisches Dashboard, das die offenen Positionen der **Top 20 Hyperliquid-Trader**
(Accounts **≥ $10 Mio**, umschaltbar nach **30-Tage-PnL** oder **ROI %**) zeigt —
Einstieg, Größe, Hebel, Liquidation, uPnL, Einstiegszeit —
plus eine Spalte, die je Coin die in **1h / 4h** geöffnete (↑) bzw. geschlossene (↓)
Kontrakt-Menge anzeigt.

Oben: **Smart-Money-Score** (−100 short … +100 long, **notional-gewichtet**) für
BTC + Whitelist (ETH, ADA, FET, ATOM) als Bias-Gate — plus eine Konsens-Leiste für
alle übrigen Coins mit ≥2 Tradern.

**Live:** https://torsten-tv.github.io/hyperliquid-dashboard/

## Wie es funktioniert

- **Daten** kommen aus der öffentlichen Hyperliquid-API (`api.hyperliquid.xyz` +
  `stats-data.hyperliquid.xyz`). Keine API-Keys, keine Secrets — alles On-Chain.
- Eine **GitHub Action** (`.github/workflows/refresh.yml`) läuft alle **15 Minuten**,
  ruft `scripts/refresh.py` auf und committet `docs/data/snapshot.json`.
- **GitHub Pages** liefert `docs/index.html` + den Snapshot → vom Handy erreichbar,
  **ohne dass ein PC läuft**.
- **„↻ Neu laden"** holt den aktuellsten Snapshot.
- **„⚡ Live 1h/4h"** fragt direkt im Browser bei Hyperliquid an (CORS getestet: ok)
  und rechnet Positionen + 1h/4h-Veränderungen in Echtzeit neu.

## Lokal ausführen

```bash
pip install -r requirements.txt
python scripts/refresh.py                 # erzeugt docs/data/snapshot.json
python -m http.server 8899 --directory docs   # http://localhost:8899
```

## snapshot.json (Schema)

```jsonc
{
  "generatedAt": "ISO",
  "generatedAtMs": 0,
  "window": "month",
  "topN": 20,
  "focus": ["BTC","ETH","ADA","FET","ATOM"],
  "traders": [{
    "addr": "0x…", "name": "…|null",
    "accountValue": 0, "monthPnl": 0, "monthRoi": 0,
    "positions": [{
      "coin": "ETH", "side": "long|short", "szi": 0,
      "entryPx": 0, "notionalUsd": 0, "leverage": "3x cross",
      "liqPx": 0, "uPnl": 0, "entryTime": 0
    }],
    "changes": {
      "h1": { "ETH": { "openedSz": 0, "closedSz": 0, "n": 0, "lastTime": 0 } },
      "h4": { /* … */ }
    }
  }],
  "aggregates": [{ "coin": "ETH", "long": 0, "short": 0,
                   "longNotional": 0, "shortNotional": 0,
                   "totalNotional": 0, "score": -100 }]  // score: -100 short … +100 long
}
```

## Konfiguration

In `scripts/refresh.py`: `WINDOW` (Ranking-Fenster), `TOP_N` (Anzahl Trader),
`MIN_ACCOUNT` (Mindest-Accountwert, default $10M), `FILL_LOOKBACK_DAYS`.
Der Snapshot enthält den **Union-Pool** aus Top-N-nach-PnL + Top-N-nach-ROI; im
Frontend schaltet der PnL/ROI-Button die sichtbaren Top 20 clientseitig um.

## Hinweise

- GitHub-Cron läuft nicht sekundengenau und kann bei Last verzögern; geplante
  Workflows werden nach 60 Tagen Repo-Inaktivität pausiert (per Hand reaktivierbar).
- Nur öffentliche Daten. **Keine Anlageberatung.**
