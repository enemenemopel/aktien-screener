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

import pandas as pd
import yfinance as yf

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

# Manuell gepflegte WKN fuer Nicht-DE-Aktien, die recherchiert wurden.
WKN_OVERRIDES = {
    "FSLR": "A0LEKM",  # First Solar Inc.
}


def get_sp500_tickers():
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    df = pd.read_html(url)[0]
    return [t.replace(".", "-") for t in df["Symbol"].tolist()]


def get_nikkei225_tickers():
    url = "https://en.wikipedia.org/wiki/Nikkei_225"
    for table in pd.read_html(url):
        if "Code" in table.columns:
            return [f"{int(code)}.T" for code in table["Code"].tolist()]
    return []


def build_universe():
    groups = {}
    for t in DAX40:
        groups[t] = "DAX40"
    for t in COMMODITIES:
        groups[t] = "Rohstoff"
    try:
        for t in get_sp500_tickers():
            groups.setdefault(t, "S&P500")
    except Exception as e:
        print("S&P 500 Liste konnte nicht geladen werden:", e)
    try:
        for t in get_nikkei225_tickers():
            groups.setdefault(t, "Nikkei225")
    except Exception as e:
        print("Nikkei 225 Liste konnte nicht geladen werden:", e)
    return groups


def get_wkn(ticker, info):
    if ticker in WKN_OVERRIDES:
        return WKN_OVERRIDES[ticker]
    isin = info.get("isin") or info.get("ISIN")
    if isin and isin.startswith("DE") and len(isin) >= 10:
        return isin[4:10]
    return "–"


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
            "begruendung": "",  # wird per Chat-Recherche auf Anfrage ergaenzt, kein Dauerlauf
        }
    except Exception as e:
        print(f"{ticker}: Fehler - {e}")
        return None


def classify(row):
    if row["drawdown_pct"] is None:
        return "NEUTRAL - unzureichende Daten"

    if row.get("is_commodity"):
        # Kein KGV bei Rohstoffen: Signal basiert nur auf Kursrueckgang vom 5J-Hoch.
        if row["drawdown_pct"] <= -20:
            return "KANDIDAT - stark gefallen (Rohstoff, kein KGV anwendbar)"
        return "NEUTRAL - kein klares Signal (Rohstoff)"

    if row["pe"] is None:
        return "NEUTRAL - unzureichende Daten"
    strong_drawdown = row["drawdown_pct"] <= -30
    cheap = row["pe"] < 15
    if strong_drawdown and cheap:
        return "KANDIDAT - stark gefallen + niedriges KGV"
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
