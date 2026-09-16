"""
Weltweiter Aktien-Screener (S&P 500 + Nikkei 225 + DAX 40 + EURO STOXX 50)

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
import math
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


def fetch_tables(url):
    """Wikipedia blockt Anfragen ohne Browser-User-Agent mit HTTP 403 -
    daher erst per requests mit Header abrufen, dann an pandas uebergeben."""
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return pd.read_html(StringIO(resp.text))


def get_sp500_tickers():
    """Volles S&P 500."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    df = fetch_tables(url)[0]
    return [t.replace(".", "-") for t in df["Symbol"].tolist()]


# Nikkei 225: Wikipedia fuehrt aktuell keine zuverlaessig scrapebare
# Konstituenten-Tabelle mehr (Spalte "Code" existiert nicht mehr auf der
# Seite) - daher eine kuratierte Liste bekannter, grosser Nikkei-225-Werte
# statt automatischem Scraping. Nicht alle 225, aber die liquidesten.
NIKKEI_CURATED = [
    "7203.T", "6758.T", "9984.T", "8306.T", "6098.T", "9432.T", "6501.T",
    "8035.T", "4063.T", "6902.T", "7267.T", "8316.T", "4568.T", "9433.T",
    "6367.T", "6981.T", "8058.T", "8031.T", "7974.T", "4661.T", "6861.T",
    "6954.T", "6857.T", "9983.T", "7201.T", "6752.T", "7751.T", "6702.T",
    "6701.T", "7752.T", "5108.T", "6503.T", "6971.T", "6594.T", "4523.T",
    "4519.T", "4502.T", "4503.T", "8309.T", "8411.T", "8604.T", "8766.T",
    "8725.T", "9022.T", "9020.T", "9021.T", "9202.T", "9201.T",
    "4755.T", "7269.T", "7270.T", "7261.T", "6301.T", "6326.T", "4452.T",
    "4911.T", "2502.T", "2503.T", "2914.T", "3382.T", "9064.T", "8002.T",
    "8001.T", "8053.T",
]


def get_eurostoxx50_tickers():
    """EURO STOXX 50 (50 groesste Eurozone-Blue-Chips) als Ersatz fuer den
    vollen STOXX Europe 600: fuer STOXX 600 gibt es keine kostenlos
    scrapebare Ticker-Tabelle mit korrekten Yahoo-Suffixen, fuer
    EURO STOXX 50 dagegen schon (Wikipedia-Tabelle mit Spalte 'Ticker')."""
    url = "https://en.wikipedia.org/wiki/EURO_STOXX_50"
    for table in fetch_tables(url):
        if "Ticker" in table.columns:
            return table["Ticker"].tolist()
    return []


def build_universe():
    groups = {}
    for t in DAX40:
        groups[t] = "DAX40"
    for t in COMMODITIES:
        groups[t] = "Rohstoff"
    for t in NIKKEI_CURATED:
        groups[t] = "Nikkei225"
    try:
        for t in get_sp500_tickers():
            groups.setdefault(t, "S&P500")
    except Exception as e:
        print("S&P 500 Liste konnte nicht geladen werden:", e)
    try:
        for t in get_eurostoxx50_tickers():
            groups.setdefault(t, "EuroStoxx50")
    except Exception as e:
        print("EURO STOXX 50 Liste konnte nicht geladen werden:", e)
    return groups


def get_wkn(ticker, info):
    if ticker in WKN_OVERRIDES:
        return WKN_OVERRIDES[ticker]
    isin = info.get("isin") or info.get("ISIN")
    if isin and isin.startswith("DE") and len(isin) >= 10:
        return isin[4:10]
    return "–"


def calc_adx(hist, period=14):
    """ADX (Average Directional Index) + DI/-DI: misst die STAERKE eines
    Trends (nicht die Richtung). >25 gilt als starker Trend."""
    high, low, close = hist["High"], hist["Low"], hist["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = ((up_move > down_move) & (up_move > 0)) * up_move
    minus_dm = ((down_move > up_move) & (down_move > 0)) * down_move
    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.ewm(alpha=1 / period, adjust=False).mean()
    last = lambda s: round(float(s.iloc[-1]), 1) if pd.notna(s.iloc[-1]) else None
    return last(adx), last(plus_di), last(minus_di)


def calc_tsi(closes, long=25, short=13):
    """TSI (True Strength Index), hier als Verhaeltnis -1 bis +1 (nicht
    x100 skaliert) analog zur vorgegebenen Punktetabelle."""
    momentum = closes.diff()
    ema1 = momentum.ewm(span=long, adjust=False).mean()
    ema2 = ema1.ewm(span=short, adjust=False).mean()
    abs_ema1 = momentum.abs().ewm(span=long, adjust=False).mean()
    abs_ema2 = abs_ema1.ewm(span=short, adjust=False).mean()
    tsi = ema2 / abs_ema2
    val = tsi.iloc[-1]
    prev = tsi.iloc[-2] if len(tsi) > 1 else None
    val = round(float(val), 3) if pd.notna(val) else None
    prev = round(float(prev), 3) if prev is not None and pd.notna(prev) else None
    return val, prev


def calc_bollinger_pctb(closes, period=20, num_std=2):
    """Bollinger %B: Position des Kurses innerhalb der Bollinger-Baender.
    <0.2 = nahe/unter dem unteren Band (potenzieller Einstiegsbereich)."""
    sma = closes.rolling(period).mean()
    std = closes.rolling(period).std()
    upper, lower = sma + num_std * std, sma - num_std * std
    band_width = upper.iloc[-1] - lower.iloc[-1]
    if pd.isna(band_width) or band_width == 0:
        return None
    return round(float((closes.iloc[-1] - lower.iloc[-1]) / band_width), 3)


def calc_volume_ratio(volumes, period=20):
    """Aktuelles Volumen als % des 20-Tage-Durchschnittsvolumens."""
    avg = volumes.rolling(period).mean().iloc[-1]
    if pd.isna(avg) or avg == 0:
        return None
    return round(float(volumes.iloc[-1] / avg * 100), 1)


def punkte_sma200(price, sma200):
    if sma200 is None:
        return None
    return 20 if price > sma200 else 0


def punkte_sma50_vs_200(sma50, sma200):
    if sma50 is None or sma200 is None:
        return None
    return 15 if sma50 > sma200 else 0


def punkte_adx(adx):
    if adx is None:
        return None
    if adx < 15:
        return 0
    if adx < 20:
        return 5
    if adx < 25:
        return 10
    return 15


def punkte_tsi(tsi, tsi_prev):
    if tsi is None:
        return None
    steigend = tsi_prev is not None and tsi > tsi_prev
    # Tabellen-Stufen; bei "0 bis +0.7"-Bereichen zaehlt der obere Wert nur,
    # wenn TSI zusaetzlich steigt (sonst eine Stufe niedriger) - das war im
    # Original nicht fuer jeden Fall exakt beziffert, das ist meine
    # konsistente Interpretation davon.
    if tsi < -0.7:
        return 0
    if tsi < 0:
        return 5
    if tsi < 0.3:
        return 12 if steigend else 5
    if tsi < 0.7:
        return 18 if steigend else 12
    return 20 if steigend else 18


def punkte_bb(pctb):
    if pctb is None:
        return None
    if pctb < 0:
        return 15
    if pctb < 0.20:
        return 20
    if pctb < 0.40:
        return 17
    if pctb < 0.60:
        return 10
    if pctb < 0.80:
        return 5
    return 0


def punkte_volumen(vol_ratio_pct):
    if vol_ratio_pct is None:
        return None
    if vol_ratio_pct < 80:
        return 0
    if vol_ratio_pct < 100:
        return 3
    if vol_ratio_pct < 120:
        return 5
    if vol_ratio_pct < 150:
        return 8
    return 10


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
        ma50_val = round(float(ma50), 2) if pd.notna(ma50) else None
        ma200_val = round(float(ma200), 2) if pd.notna(ma200) else None

        adx14, plus_di14, minus_di14 = calc_adx(hist)
        tsi, tsi_prev = calc_tsi(hist["Close"])
        bb_pctb = calc_bollinger_pctb(hist["Close"])
        vol_ratio_pct = calc_volume_ratio(hist["Volume"]) if "Volume" in hist.columns else None

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
            "pe": round(pe, 1) if isinstance(pe, (int, float)) and not math.isnan(pe) else None,
            "ma50": ma50_val,
            "ma200": ma200_val,
            "ma_5y": round(ma_5y, 2),
            "above_ma200": bool(price > ma200) if pd.notna(ma200) else None,
            "adx14": adx14,
            "plus_di14": plus_di14,
            "minus_di14": minus_di14,
            "tsi": tsi,
            "tsi_prev": tsi_prev,
            "bb_pctb": bb_pctb,
            "vol_ratio_pct": vol_ratio_pct,
            "begruendung": "",  # wird per Chat-Recherche auf Anfrage ergaenzt, kein Dauerlauf
        }
    except Exception as e:
        print(f"{ticker}: Fehler - {e}")
        return None


def tech_score(row):
    """100-Punkte-System fuer die Qualitaet des technischen Setups:
    SMA200 (20) + SMA50-vs-200 (15) + ADX (15) + TSI (20) + Bollinger %B (20)
    + Volumen (10). Fehlt eine Komponente (z.B. zu kurze Historie), zaehlt
    sie mit 0 Punkten, wird aber separat als 'unvollstaendig' markiert."""
    komponenten = {
        "sma200": punkte_sma200(row["price"], row.get("ma200")),
        "sma50_vs_200": punkte_sma50_vs_200(row.get("ma50"), row.get("ma200")),
        "adx": punkte_adx(row.get("adx14")),
        "tsi": punkte_tsi(row.get("tsi"), row.get("tsi_prev")),
        "bb": punkte_bb(row.get("bb_pctb")),
        "volumen": punkte_volumen(row.get("vol_ratio_pct")),
    }
    vollstaendig = all(v is not None for v in komponenten.values())
    score = sum(v or 0 for v in komponenten.values())
    return score, 100, vollstaendig


def setup_kategorie(score):
    if score >= 90:
        return "sehr starkes Setup"
    if score >= 80:
        return "starkes Setup"
    if score >= 65:
        return "interessantes Setup"
    if score >= 50:
        return "schwaches Setup"
    return "kein interessantes Setup"


def classify(row):
    if row["drawdown_pct"] is None:
        return "NEUTRAL - unzureichende Daten"

    score, max_score, vollstaendig = tech_score(row)
    kategorie = setup_kategorie(score)
    unvollst = "" if vollstaendig else " (unvollstaendige Daten)"

    if row.get("is_commodity"):
        # Kein KGV bei Rohstoffen: Signal basiert auf Kursrueckgang + Technik-Score.
        if row["drawdown_pct"] <= -20:
            if score >= 65:
                return f"KANDIDAT - stark gefallen + {kategorie} (Score {score}/{max_score}, Rohstoff){unvollst}"
            return f"BEOBACHTEN - stark gefallen, aber {kategorie} (Score {score}/{max_score}, Rohstoff){unvollst}"
        return "NEUTRAL - kein klares Signal (Rohstoff)"

    if row["pe"] is None:
        return "NEUTRAL - unzureichende Daten"
    strong_drawdown = row["drawdown_pct"] <= -30
    cheap = row["pe"] < 15

    if strong_drawdown and cheap:
        if score >= 80:
            return f"KANDIDAT - fundamental guenstig + {kategorie} (Score {score}/{max_score}){unvollst}"
        if score >= 65:
            return f"KANDIDAT - fundamental guenstig, {kategorie} (Score {score}/{max_score}){unvollst}"
        return f"BEOBACHTEN - fundamental guenstig, aber {kategorie} (Score {score}/{max_score}){unvollst}"
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

    from collections import Counter
    breakdown = Counter(universe.values())
    for group, count in sorted(breakdown.items()):
        print(f"  {group}: {count}")

    results = []
    for i, ticker in enumerate(tickers):
        row = analyze_ticker(ticker, universe[ticker])
        if row:
            row["tech_score"], row["tech_score_max"], row["tech_score_vollstaendig"] = tech_score(row)
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

    def clean(obj):
        """Ersetzt NaN/Infinity (ungueltig in JSON) rekursiv durch null,
        damit ein einzelner defekter Wert nie die ganze Datei unlesbar macht."""
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return None
        if isinstance(obj, dict):
            return {k: clean(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [clean(v) for v in obj]
        return obj

    output = clean(output)

    with open("docs/data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
