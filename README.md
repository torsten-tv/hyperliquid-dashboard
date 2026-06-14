# Hyperliquid Top-Trader Dashboard

Ein statisches Dashboard, das die offenen Positionen der **Top 20 Hyperliquid-Trader**
(Accounts **≥ $10 Mio**, umschaltbar nach **30-Tage-PnL** oder **ROI %**) zeigt —
Einstieg, Größe, Hebel, Liquidation, uPnL, Einstiegszeit —
plus eine Spalte, die je Coin die in **1h / 4h** geöffnete (↑) bzw. geschlossene (↓)
Kontrakt-Menge anzeigt.

Es werden nur diese Coins verfolgt (Whitelist `COINS`): **BTC, ETH, ATOM, TAO, XRP,
SOL, BNB, DOGE, AVAX** — alles andere wird verworfen. Oben je Coin ein
**Smart-Money-Score** (−100 short … +100 long, **notional-gewichtet**) als Bias-Gate.

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
      "liqPx": 0, "uPnl": 0,
      "entryTime": 0,    // exakte Eröffnung (Fill aus startPosition 0 / Flip) oder null
      "entrySince": 0    // Fallback-Untergrenze "≥": ältester sichtbarer Fill, wenn
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

In `scripts/refresh.py`: `COINS` (Coin-Whitelist), `WINDOW` (Ranking-Fenster),
`TOP_N` (Anzahl Trader), `MIN_ACCOUNT` (Mindest-Accountwert, default $10M),
`CANDIDATES` (Scan-Tiefe je Metrik), `FILL_LOOKBACK_DAYS`.

Nur **aktive** Accounts (mit offener Position) kommen rein — die größten Accounts sind
oft flat. Ablauf: Phase 1 holt günstig die Positionen von `CANDIDATES` Kandidaten je
Metrik (clearinghouseState), filtert auf aktive, wählt Top-N nach PnL + ROI; Phase 2
holt die teuren Fills (userFillsByTime) nur für die Ausgewählten. Der PnL/ROI-Button
schaltet die sichtbaren Top 20 clientseitig um.

## Einstiegszeit

`entryTime` ist die echte Eröffnung der aktuellen Position (jüngster Fill mit
`startPosition` = 0 bzw. Flip in die aktuelle Richtung). Findet die API diesen Fill
nicht (sehr aktive/HFT-Accounts geben nur die ~2000 jüngsten Fills preis, sehr alte
Positionen fallen raus), wird stattdessen `entrySince` gesetzt — der älteste sichtbare
Fill als Untergrenze, im UI als „≥ Datum". Ist gar kein Fill abrufbar: „—".

## Hinweise

- GitHub-Cron läuft nicht sekundengenau und kann bei Last verzögern; geplante
  Workflows werden nach 60 Tagen Repo-Inaktivität pausiert (per Hand reaktivierbar).
- Nur öffentliche Daten. **Keine Anlageberatung.**
