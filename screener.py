"""
Weltweiter Aktien-Screener (Basis-Version: S&P 500 + Nikkei 225 + DAX 40)

Sucht nach Aktien, die stark gefallen sind UND fundamental (KGV) guenstig
bewertet sind. Kein erzwungenes Signal: wenn die Daten nicht klar dafuer
sprechen, wird "NEUTRAL" ausgegeben statt eine Aktie schoenzurechnen.

Datenquelle: Yahoo Finance ueber yfinance (Gratis, ca. 15 Min. verzoegert).

WKN-Hinweis: Fuer deutsche Aktien (.DE) laesst sich die WKN aus der ISIN
ableiten (WKN = Zeichen 5-10 einer DE-ISIN). Fuer US-/JP-Aktien gibt es
keine kostenlose, verlaessliche WKN-Quelle - dafuer WKN_OVERRIDES pflegen
oder spaeter eine Boerse-Datenquelle (z. B. onvista) ergaenzen.
"""

import json
import time
from datetime import datetime, timezone
from io import StringIO

import pandas as pd
import requests
import yfinance as yf

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

DAX40 = [
    "SAP.DE", "SIE.DE", "ALV.DE", "DTE.DE", "AIR.DE", "MBG.DE", "BAS.DE",
    "BAYN.DE", "BMW.DE", "VOW3.DE", "DBK.DE", "MUV2.DE", "RWE.DE", "IFX.DE",
    "ADS.DE", "HEN3.DE", "LIN.DE", "FRE.DE", "MRK.DE", "VNA.DE", "CON.DE",
    "HEI.DE", "ENR.DE", "SY1.DE", "ZAL.DE", "P911.DE", "QIA.DE", "RHM.DE",
    "SRT3.DE", "BEI.DE", "1COV.DE", "EOAN.DE", "FME.DE", "MTX.DE", "SHL.DE",
    "BNR.DE", "CBK.DE", "DHER.DE", "HNR1.DE", "PAH3.DE",
]

# Rohstoffe: kein KGV vorhanden, werden in classify() gesondert behandelt.
COMMODITIES = {
    "GC=F": "Gold",
    "SI=F": "Silber",
    "HG=F": "Kupfer",
}

# Manuell gepflegte WKN. Fuer DAX40 fest hinterlegt (oeffentlich bekannt,
# aendert sich praktisch nie) - yfinance liefert ISIN nicht zuverlaessig,
# daher lohnt sich die Ableitung darueber in der Praxis kaum.
WKN_OVERRIDES = {
    "FSLR": "A0LEKM",  # First Solar Inc.
    "SAP.DE": "716460", "SIE.DE": "723610", "ALV.DE": "840400",
    "DTE.DE": "555750", "AIR.DE": "938914", "MBG.DE": "710000",
    "BAS.DE": "BASF11", "BAYN.DE": "BAY001", "BMW.DE": "519000",
    "VOW3.DE": "766403", "DBK.DE": "514000", "MUV2.DE": "843002",
    "RWE.DE": "703712", "IFX.DE": "623100", "ADS.DE": "A1EWWW",
    "HEN3.DE": "604843", "LIN.DE": "A2DSYC", "FRE.DE": "578560",
    "MRK.DE": "659990", "VNA.DE": "A1ML7J", "CON.DE": "543900",
    "HEI.DE": "604700", "ENR.DE": "ENER6Y", "SY1.DE": "SYM999",
    "ZAL.DE": "ZAL111", "P911.DE": "PAG911", "QIA.DE": "A2DKCH",
    "RHM.DE": "703000", "SRT3.DE": "716563", "BEI.DE": "520000",
    "1COV.DE": "606214", "EOAN.DE": "ENAG99", "FME.DE": "578580",
    "MTX.DE": "A0D9PT", "SHL.DE": "SHL100", "BNR.DE": "A1DAHH",
    "CBK.DE": "CBK100", "DHER.DE": "A2E4K4", "HNR1.DE": "840221",
    "PAH3.DE": "PAH003",
}


NIKKEI_TOP20 = [
    "7203.T",  # Toyota
    "6758.T",  # Sony
    "9984.T",  # SoftBank Group
    "8306.T",  # Mitsubishi UFJ
    "6098.T",  # Recruit Holdings
    "9432.T",  # NTT
    "6501.T",  # Hitachi
    "8035.T",  # Tokyo Electron
    "4063.T",  # Shin-Etsu Chemical
    "6902.T",  # Denso
    "7267.T",  # Honda
    "8316.T",  # Sumitomo Mitsui
    "4568.T",  # Daiichi Sankyo
    "9433.T",  # KDDI
    "6367.T",  # Daikin
    "6981.T",  # Murata
    "8058.T",  # Mitsubishi Corp
    "8031.T",  # Mitsui & Co
    "7974.T",  # Nintendo
    "4661.T",  # Oriental Land
]


def fetch_tables(url):
    """Wikipedia blockt Anfragen ohne Browser-User-Agent mit HTTP 403 -
    daher erst per requests mit Header abrufen, dann an pandas uebergeben."""
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return pd.read_html(StringIO(resp.text))


def get_sp100_tickers():
    """S&P 100 statt S&P 500: die ~100 groessten, liquidesten US-Aktien.
    Deutlich schnellerer Lauf als das volle S&P 500, und als Naeherung fuer
    "groesste Marktkapitalisierung" gut geeignet, ohne vorher Marktkap-Daten
    abrufen zu muessen (das waere selbst der langsame Teil)."""
    url = "https://en.wikipedia.org/wiki/S%26P_100"
    for table in fetch_tables(url):
        if "Symbol" in table.columns:
            return [t.replace(".", "-") for t in table["Symbol"].tolist()]
    return []


def build_universe():
    groups = {}
    for t in DAX40:
        groups[t] = "DAX40"
    for t in COMMODITIES:
        groups[t] = "Rohstoff"
    for t in NIKKEI_TOP20:
        groups[t] = "Nikkei225"
    try:
        for t in get_sp100_tickers():
            groups.setdefault(t, "S&P100")
    except Exception as e:
        print("S&P 100 Liste konnte nicht geladen werden:", e)
    return groups


def get_wkn(ticker, info):
    if ticker in WKN_OVERRIDES:
        return WKN_OVERRIDES[ticker]
    isin = info.get("isin") or info.get("ISIN")
    if isin and isin.startswith("DE") and len(isin) >= 10:
        return isin[4:10]
    return "–"


def calc_rsi(closes, period=14):
    """RSI (Relative Strength Index): misst Staerke/Geschwindigkeit von
    Kursbewegungen auf einer Skala 0-100. < 30 gilt als "ueberverkauft"."""
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    last_gain, last_loss = avg_gain.iloc[-1], avg_loss.iloc[-1]
    if pd.isna(last_gain) or pd.isna(last_loss):
        return None
    if last_loss == 0:
        return 100.0
    rs = last_gain / last_loss
    return round(100 - (100 / (1 + rs)), 1)


def calc_stochastic(hist, period=14):
    """Stochastik %K: Position des aktuellen Kurses in seiner juengsten
    Hoch/Tief-Spanne, Skala 0-100. < 20 gilt als "ueberverkauft"."""
    low_min = hist["Low"].rolling(period).min().iloc[-1]
    high_max = hist["High"].rolling(period).max().iloc[-1]
    close = hist["Close"].iloc[-1]
    if pd.isna(low_min) or pd.isna(high_max) or high_max == low_min:
        return None
    return round((close - low_min) / (high_max - low_min) * 100, 1)


def calc_momentum(closes, period=10):
    """Momentum: reine prozentuale Kursveraenderung ueber die letzten
    `period` Tage. Positiv = Aufwaertstrend hat bereits begonnen."""
    if len(closes) <= period:
        return None
    prev, curr = closes.iloc[-period - 1], closes.iloc[-1]
    if prev == 0:
        return None
    return round((curr - prev) / prev * 100, 1)


def analyze_ticker(ticker, index_group):
    try:
        tk = yf.Ticker(ticker)
        info = tk.info
        hist = tk.history(period="5y", interval="1d", auto_adjust=True)
        if hist.empty or len(hist) < 250:
            return None

        price = float(hist["Close"].iloc[-1])
        high_5y = float(hist["Close"].max())
        drawdown_pct = (price / high_5y - 1) * 100

        ma50 = hist["Close"].rolling(50).mean().iloc[-1]
        ma200 = hist["Close"].rolling(200).mean().iloc[-1]
        ma_5y = float(hist["Close"].mean())

        rsi14 = calc_rsi(hist["Close"])
        stoch_k14 = calc_stochastic(hist)
        momentum10 = calc_momentum(hist["Close"])

        pe = info.get("trailingPE")

        is_commodity = ticker in COMMODITIES
        return {
            "ticker": ticker,  # intern/fuer spaetere Schein-Zuordnung, wird im Dashboard nicht angezeigt
            "index_group": index_group,
            "wkn": "–" if is_commodity else get_wkn(ticker, info),
            "name": COMMODITIES[ticker] if is_commodity else info.get("shortName", ticker),
            "sector": "Rohstoff" if is_commodity else info.get("sector", "unbekannt"),
            "is_commodity": is_commodity,
            "price": round(price, 2),
            "currency": info.get("currency", ""),
            "_high_5y": round(high_5y, 2),  # nur intern fuer drawdown_pct, nicht im Export
            "drawdown_pct": round(drawdown_pct, 1),
            "pe": round(pe, 1) if pe else None,
            "ma50": round(float(ma50), 2) if pd.notna(ma50) else None,
            "ma200": round(float(ma200), 2) if pd.notna(ma200) else None,
            "ma_5y": round(ma_5y, 2),
            "above_ma200": bool(price > ma200) if pd.notna(ma200) else None,
            "rsi14": rsi14,
            "stoch_k14": stoch_k14,
            "momentum10": momentum10,
            "begruendung": "",  # wird per Chat-Recherche auf Anfrage ergaenzt, kein Dauerlauf
        }
    except Exception as e:
        print(f"{ticker}: Fehler - {e}")
        return None


def classify(row):
    if row["drawdown_pct"] is None:
        return "NEUTRAL - unzureichende Daten"

    technisch_ueberverkauft = (
        (row.get("rsi14") is not None and row["rsi14"] < 30)
        or (row.get("stoch_k14") is not None and row["stoch_k14"] < 20)
    )
    momentum_dreht = row.get("momentum10") is not None and row["momentum10"] > 0

    if row.get("is_commodity"):
        # Kein KGV bei Rohstoffen: Signal basiert auf Kursrueckgang + Technik.
        if row["drawdown_pct"] <= -20:
            if technisch_ueberverkauft or momentum_dreht:
                return "KANDIDAT - stark gefallen + technisch bestaetigt (Rohstoff)"
            return "BEOBACHTEN - stark gefallen, aber noch kein technischer Wendepunkt (Rohstoff)"
        return "NEUTRAL - kein klares Signal (Rohstoff)"

    if row["pe"] is None:
        return "NEUTRAL - unzureichende Daten"
    strong_drawdown = row["drawdown_pct"] <= -30
    cheap = row["pe"] < 15

    if strong_drawdown and cheap:
        if technisch_ueberverkauft and momentum_dreht:
            return "KANDIDAT - fundamental + technisch stark bestaetigt"
        if technisch_ueberverkauft or momentum_dreht:
            return "KANDIDAT - fundamental guenstig, teilweise technisch bestaetigt"
        return "BEOBACHTEN - fundamental guenstig, aber noch kein technischer Wendepunkt"
    if strong_drawdown and not cheap:
        return "BEOBACHTEN - stark gefallen, aber (noch) nicht guenstig"
    return "NEUTRAL - kein klares Signal"


def empfehlung_aus_signal(signal):
    if signal.startswith("KANDIDAT"):
        return "Long"
    if signal.startswith("BEOBACHTEN"):
        return "Beobachten"
    return "–"


def main():
    universe = build_universe()
    tickers = sorted(universe.keys())
    print(f"{len(tickers)} Ticker im Universum")

    results = []
    for i, ticker in enumerate(tickers):
        row = analyze_ticker(ticker, universe[ticker])
        if row:
            row["signal"] = classify(row)
            row["empfehlung"] = empfehlung_aus_signal(row["signal"])
            results.append(row)
        if i % 25 == 0:
            print(f"{i}/{len(tickers)} verarbeitet")
        time.sleep(0.3)

    results.sort(key=lambda r: r["drawdown_pct"])

    output = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "data_note": "Kurse ueber Yahoo-Finance-Gratis-API, ca. 15 Min. verzoegert. Keine Anlageberatung.",
        "universe_size": len(tickers),
        "results": results,
    }

    with open("docs/data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
