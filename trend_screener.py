"""
Trend-Pullback-Screener (S&P 500, DAX 40, EURO STOXX 50)

Sucht Aktien im uebergeordneten Aufwaertstrend mit kontrollierter Korrektur
und frischem Einstiegssignal. ALLE technischen Indikatoren werden hier aus
den Tageskursen selbst berechnet (keine fremden Buy-/Sell-Signale).

Datenquelle: Yahoo Finance ueber yfinance (kostenlos, ca. 15 Min. verzoegert).
Kein erzwungenes Ergebnis: Kriterien werden NICHT gelockert. Wenn nichts
alle Pflichtbedingungen erfuellt, ist die Trefferliste leer.

Ehrliche Grenzen der Datenquelle:
- yfinance liefert Konsensschaetzungen nur fuer das laufende und das naechste
  Geschaeftsjahr. 2028 ist damit NICHT verifiziert, solange keine
  consensus.csv mit 2028-Werten vorliegt (siehe load_consensus). Ohne 2028
  kann keine Aktie automatisch als "A" eingestuft werden.
- FCF-Wachstum, Insiderverkaeufe, Guidance, Analystenrevisionen und
  Gewinnwarnungen sind nicht automatisch pruefbar -> "nicht verifiziert".
"""

import json
import math
import os
import time
from datetime import date, datetime, timezone

import pandas as pd
import yfinance as yf

from screener import build_universe, get_wkn

# ----------------------------------------------------------------------------
# Parameter - alle Schwellen an einer Stelle
# ----------------------------------------------------------------------------
SMA_LEN = 40
SMA_RISING_LOOKBACK = 5            # SMA40 heute > SMA40 vor 5 Handelstagen
SMA_DIST_PREF = (0.0, 15.0)        # bevorzugter Abstand Kurs zu SMA40 in %

ICHI_TENKAN, ICHI_KIJUN, ICHI_SPAN_B, ICHI_DISP = 9, 26, 52, 26

RSL_SHORT, RSL_LONG = 30, 250
RSL_SHORT_MIN, RSL_LONG_MIN = 1.05, 1.20

RSI_LEN = 14
RSI_PREF = (35.0, 60.0)
RSI_MAX = 70.0                     # darueber = ueberkauft

STOCH_N, STOCH_SLOW, STOCH_D = 14, 3, 3
STOCH_MAX = 50.0
CROSS_LOOKBACK = 2                 # Kreuz heute oder in den letzten 2 Handelstagen = "frisch"
CROSS_PENDING_GAP = 3.0            # %K max. 3 Punkte unter %D und steigend = "steht bevor"

CORR_LOOKBACK = 60                 # Referenzhoch der letzten 60 Handelstage
CORR_RANGE = (-25.0, -5.0)         # Korrektur zwischen -5 % und -25 % vom Hoch

PEG_MAX = 1.5                      # "angemessen" = 0 < PEG <= 1.5
GROWTH_MIN = 50.0                  # % pro Jahr (EPS und/oder Umsatz)
YEARS = (2026, 2027, 2028)

MIN_BARS = RSL_LONG + 30
MIN_MARKET_CAP = 2e9               # Waehrung des Titels, grobe Untergrenze gegen Kleinstwerte
MIN_AVG_TURNOVER = 10e6            # Durchschnittlicher Tagesumsatz (20T) in Waehrung des Titels
STALE_DAYS = 5                     # aelter als 5 Tage gegenueber dem neuesten Datenstand = ausgeschlossen

BENCHMARKS = {
    "S&P500": ("^GSPC", "S&P 500"),
    "DAX40": ("^GDAXI", "DAX"),
    "EuroStoxx50": ("^STOXX50E", "EURO STOXX 50"),
}

# Die 12 Kriterien in fester Reihenfolge (Schluessel, Anzeigename)
CRITERIA = [
    ("kurs_ueber_sma40", "Kurs > SMA 40"),
    ("sma40_steigend", "SMA 40 steigend"),
    ("ichimoku_bullish", "Ichimoku bullish"),
    ("korrektur", "Korrektur vorhanden"),
    ("rsi_nicht_ueberkauft", "RSI nicht ueberkauft"),
    ("stoch_unter_50", "Slow Stochastic < 50"),
    ("stoch_kreuz_bullisch", "Bullisches %K/%D-Kreuz"),
    ("rsl30", "RSL 30T > 1,05"),
    ("rsl250", "RSL 250T > 1,20"),
    ("kgv_angemessen", "KGV/PEG angemessen"),
    ("wachstum_2026_2028", "Wachstum 2026-2028 > 50 %"),
    ("keine_warnsignale", "Keine gravierenden Warnsignale"),
]
AUTO_KEYS = [k for k, _ in CRITERIA if k != "keine_warnsignale"]
TECH_KEYS = AUTO_KEYS[:9]
TREND_BASE_KEYS = ["kurs_ueber_sma40", "sma40_steigend", "ichimoku_bullish", "rsl250"]

RSL_METHOD = (
    "RSL(N) = (Kurs heute / Kurs vor N Handelstagen) / (Benchmark heute / Benchmark vor N Handelstagen). "
    "Benchmark: S&P 500 fuer US-Aktien, DAX fuer DAX-Werte, EURO STOXX 50 fuer uebrige Euro-Werte. "
    "Zusaetzlich wird die klassische RSL nach Levy (Kurs / Durchschnittskurs der letzten N Tage) "
    "als Zusatzinfo ausgewiesen; fuer die Pflichtbedingung zaehlt der Benchmark-Vergleich."
)


# ----------------------------------------------------------------------------
# Hilfsfunktionen
# ----------------------------------------------------------------------------
def r(x, nd=2):
    """Rundet und macht aus NaN/None sauber None (JSON-tauglich)."""
    if x is None:
        return None
    try:
        if pd.isna(x) or math.isinf(x):
            return None
    except TypeError:
        return None
    return round(float(x), nd)


def gt(a, b):
    """a > b oder None, falls ein Wert fehlt (= nicht verifiziert)."""
    if a is None or b is None or pd.isna(a) or pd.isna(b):
        return None
    return bool(a > b)


def ge(a, b):
    if a is None or b is None or pd.isna(a) or pd.isna(b):
        return None
    return bool(a >= b)


def all_or_none(*vals):
    """True nur wenn alle True; False wenn mindestens ein False; sonst None."""
    if any(v is False for v in vals):
        return False
    if any(v is None for v in vals):
        return None
    return True


def clean_index(df):
    """Zeitzonen entfernen und auf Datum normalisieren, damit Aktie und
    Benchmark (verschiedene Boersen) auf gleichen Tagen ausgerichtet werden."""
    idx = pd.DatetimeIndex(df.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    df = df.copy()
    df.index = idx.normalize()
    return df[~df.index.duplicated(keep="last")]


def load_history(symbol, period="2y"):
    try:
        hist = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=True)
    except Exception as e:
        print(f"{symbol}: Kursabruf fehlgeschlagen - {e}")
        return None
    if hist is None or hist.empty:
        return None
    return clean_index(hist).dropna(subset=["Close", "High", "Low"])


# ----------------------------------------------------------------------------
# Indikatoren (selbst berechnet)
# ----------------------------------------------------------------------------
def sma(series, n):
    return series.rolling(n).mean()


def rsi_wilder(close, n=RSI_LEN):
    """RSI nach Wilder (geglaettet mit alpha = 1/n)."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    avg_loss = loss.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def ichimoku(high, low, tenkan_n=ICHI_TENKAN, kijun_n=ICHI_KIJUN,
             span_b_n=ICHI_SPAN_B, disp=ICHI_DISP):
    """Tenkan, Kijun, Senkou A, Senkou B. Die Wolke, die HEUTE gilt, ist die
    vor `disp` Tagen berechnete und um `disp` nach vorn verschobene Wolke -
    deshalb genuegt shift(disp) und der Zugriff auf den letzten Index."""
    tenkan = (high.rolling(tenkan_n).max() + low.rolling(tenkan_n).min()) / 2
    kijun = (high.rolling(kijun_n).max() + low.rolling(kijun_n).min()) / 2
    span_a = ((tenkan + kijun) / 2).shift(disp)
    span_b = ((high.rolling(span_b_n).max() + low.rolling(span_b_n).min()) / 2).shift(disp)
    return tenkan, kijun, span_a, span_b


def slow_stochastic(high, low, close, n=STOCH_N, slowing=STOCH_SLOW, d=STOCH_D):
    """Slow Stochastic 14/3/3: Rohwert %K(14), geglaettet mit SMA(3) = slow %K,
    davon SMA(3) = %D."""
    ll = low.rolling(n).min()
    hh = high.rolling(n).max()
    rng = (hh - ll).replace(0, float("nan"))
    raw_k = 100 * (close - ll) / rng
    slow_k = raw_k.rolling(slowing).mean()
    slow_d = slow_k.rolling(d).mean()
    return slow_k, slow_d


def stoch_cross_state(k, d, lookback=CROSS_LOOKBACK):
    """Unterscheidet eindeutig:
    'erfolgt'   - %K hat %D in den letzten `lookback` Handelstagen von unten
                  nach oben gekreuzt und liegt weiterhin darueber,
    'bevorstehend' - %K liegt knapp unter %D und steigt,
    'kein'      - sonst.
    Rueckgabe: (Zustand, Tage seit Kreuz oder None, %K-Niveau beim Kreuz oder None)."""
    if len(k) < lookback + 2 or pd.isna(k.iloc[-1]) or pd.isna(d.iloc[-1]):
        return None, None, None
    for i in range(1, lookback + 1):          # i=1: heute gegen gestern
        now, prev = -i, -(i + 1)
        if any(pd.isna(x) for x in (k.iloc[now], d.iloc[now], k.iloc[prev], d.iloc[prev])):
            continue
        if k.iloc[prev] <= d.iloc[prev] and k.iloc[now] > d.iloc[now] and k.iloc[-1] > d.iloc[-1]:
            return "erfolgt", i - 1, float(k.iloc[now])
    gap = d.iloc[-1] - k.iloc[-1]
    if 0 < gap <= CROSS_PENDING_GAP and k.iloc[-1] > k.iloc[-2]:
        return "bevorstehend", None, None
    return "kein", None, None


def rsl_benchmark(stock, bench, n):
    """RSL(n) als relative Performance gegenueber der Benchmark."""
    if bench is None or len(stock) <= n:
        return None
    b = bench.reindex(stock.index).ffill()
    if pd.isna(b.iloc[-1]) or pd.isna(b.iloc[-1 - n]) or b.iloc[-1 - n] == 0:
        return None
    return float((stock.iloc[-1] / stock.iloc[-1 - n]) / (b.iloc[-1] / b.iloc[-1 - n]))


def rsl_levy(stock, n):
    avg = stock.iloc[-n:].mean()
    return float(stock.iloc[-1] / avg) if avg else None


# ----------------------------------------------------------------------------
# Stufe 1: Technik
# ----------------------------------------------------------------------------
def analyze_technical(ticker, group, bench_close):
    hist = load_history(ticker)
    if hist is None or len(hist) < MIN_BARS:
        return None
    close, high, low = hist["Close"], hist["High"], hist["Low"]
    price = float(close.iloc[-1])

    sma40 = sma(close, SMA_LEN)
    sma_now = sma40.iloc[-1]
    sma_prev = sma40.iloc[-1 - SMA_RISING_LOOKBACK]
    dist = (price / sma_now - 1) * 100 if pd.notna(sma_now) else None

    tenkan, kijun, span_a, span_b = ichimoku(high, low)
    sa, sb = span_a.iloc[-1], span_b.iloc[-1]
    cloud_top = max(sa, sb) if pd.notna(sa) and pd.notna(sb) else None
    cloud_bottom = min(sa, sb) if pd.notna(sa) and pd.notna(sb) else None
    ichi_bullish = all_or_none(
        gt(price, cloud_top),
        gt(sa, sb),
        ge(tenkan.iloc[-1], kijun.iloc[-1]),
    )

    rsi = rsi_wilder(close)
    rsi_now = rsi.iloc[-1]
    rsi_turning = bool(rsi.iloc[-1] >= rsi.iloc[-2]) if pd.notna(rsi.iloc[-2]) else None

    slow_k, slow_d = slow_stochastic(high, low, close)
    cross_state, cross_days_ago, cross_level = stoch_cross_state(slow_k, slow_d)

    ref_high = close.iloc[-CORR_LOOKBACK:].max()
    drawdown = (price / ref_high - 1) * 100
    corr_ok = bool(CORR_RANGE[0] <= drawdown <= CORR_RANGE[1])

    rsl30 = rsl_benchmark(close, bench_close, RSL_SHORT)
    rsl250 = rsl_benchmark(close, bench_close, RSL_LONG)

    turnover = (close * hist["Volume"]).rolling(20).mean().iloc[-1] if "Volume" in hist else None
    liquid = None if turnover is None or pd.isna(turnover) else bool(turnover >= MIN_AVG_TURNOVER)

    crit = {
        "kurs_ueber_sma40": gt(price, sma_now),
        "sma40_steigend": gt(sma_now, sma_prev),
        "ichimoku_bullish": ichi_bullish,
        "korrektur": corr_ok,
        "rsi_nicht_ueberkauft": None if pd.isna(rsi_now) else bool(rsi_now <= RSI_MAX),
        "stoch_unter_50": None if pd.isna(slow_k.iloc[-1]) else bool(slow_k.iloc[-1] < STOCH_MAX),
        "stoch_kreuz_bullisch": None if cross_state is None else cross_state == "erfolgt",
        "rsl30": None if rsl30 is None else bool(rsl30 > RSL_SHORT_MIN),
        "rsl250": None if rsl250 is None else bool(rsl250 > RSL_LONG_MIN),
    }

    return {
        "ticker": ticker,
        "index_group": group,
        "benchmark": BENCHMARKS[group][1],
        "datenstand": close.index[-1].date().isoformat(),
        "liquide": liquid,
        "preis": r(price),
        "sma40": r(sma_now),
        "sma40_abstand_pct": r(dist, 1),
        "sma40_abstand_bevorzugt": None if dist is None else bool(SMA_DIST_PREF[0] <= dist <= SMA_DIST_PREF[1]),
        "ichimoku": {
            "tenkan": r(tenkan.iloc[-1]), "kijun": r(kijun.iloc[-1]),
            "senkou_a": r(sa), "senkou_b": r(sb),
            "wolke_oben": r(cloud_top), "wolke_unten": r(cloud_bottom),
            "kurs_ueber_wolke": gt(price, cloud_top),
            "wolke_bullish": gt(sa, sb),
            "tenkan_ueber_kijun": ge(tenkan.iloc[-1], kijun.iloc[-1]),
            "kijun_als_stuetze": ge(price, kijun.iloc[-1]),
        },
        "rsi14": r(rsi_now, 1),
        "rsi_drehend_hoch": rsi_turning,
        "rsi_im_bevorzugten_bereich": None if pd.isna(rsi_now) else bool(RSI_PREF[0] <= rsi_now <= RSI_PREF[1]),
        "stoch_k": r(slow_k.iloc[-1], 1),
        "stoch_d": r(slow_d.iloc[-1], 1),
        "stoch_kreuz": cross_state,             # erfolgt / bevorstehend / kein
        "stoch_kreuz_tage_her": cross_days_ago,
        "stoch_kreuz_niveau": r(cross_level, 1),
        "stoch_kreuz_im_sweetspot": None if cross_level is None else bool(20 <= cross_level <= 40),
        "rsl30": r(rsl30, 3),
        "rsl250": r(rsl250, 3),
        "rsl30_levy": r(rsl_levy(close, RSL_SHORT), 3),
        "rsl250_levy": r(rsl_levy(close, RSL_LONG), 3),
        "korrektur_vom_60t_hoch_pct": r(drawdown, 1),
        "hoch_60t": r(ref_high),
        "tief_10t": r(low.iloc[-10:].min()),
        "kriterien": crit,
    }


# ----------------------------------------------------------------------------
# Stufe 2: Fundamentaldaten
# ----------------------------------------------------------------------------
def load_consensus(path="consensus.csv"):
    """Optional: manuell/aus anderer Quelle gepflegte Konsensdaten.
    Spalten (Prozent gegenueber Vorjahr): ticker, rev_2026, rev_2027, rev_2028,
    eps_2026, eps_2027, eps_2028. Ueberschreibt yfinance-Werte und ist die
    einzige Moeglichkeit, 2028 zu verifizieren."""
    if not os.path.exists(path):
        return {}
    df = pd.read_csv(path)
    df["ticker"] = df["ticker"].astype(str).str.strip()
    return {row["ticker"]: row.to_dict() for _, row in df.iterrows()}


def _est_growth(df, key):
    try:
        v = df.loc[key, "growth"]
        return float(v) * 100 if pd.notna(v) else None
    except Exception:
        return None


def fetch_fundamentals(ticker, consensus):
    tk = yf.Ticker(ticker)
    try:
        info = tk.info or {}
    except Exception:
        info = {}

    growth = {y: {"rev": None, "eps": None} for y in YEARS}
    quelle = {}
    fy0 = None
    try:
        nfy = info.get("nextFiscalYearEnd")
        if nfy:
            fy0 = datetime.fromtimestamp(nfy, tz=timezone.utc).year
    except Exception:
        pass
    if fy0 is None:
        fy0 = date.today().year

    try:
        rev_est, eps_est = tk.revenue_estimate, tk.earnings_estimate
    except Exception:
        rev_est = eps_est = None
    for fy, key in ((fy0, "0y"), (fy0 + 1, "+1y")):
        if fy in growth:
            if rev_est is not None:
                growth[fy]["rev"] = _est_growth(rev_est, key)
            if eps_est is not None:
                growth[fy]["eps"] = _est_growth(eps_est, key)
            quelle[fy] = "yfinance-Konsens"

    c = consensus.get(ticker)
    if c:
        for y in YEARS:
            for kind in ("rev", "eps"):
                v = c.get(f"{kind}_{y}")
                if v is not None and pd.notna(v):
                    growth[y][kind] = float(v)
                    quelle[y] = "consensus.csv"

    statuses = []
    for y in YEARS:
        vals = [v for v in (growth[y]["rev"], growth[y]["eps"]) if v is not None]
        statuses.append(None if not vals else bool(any(v > GROWTH_MIN for v in vals)))
    growth_ok = all_or_none(*statuses)

    def cum_cagr(kind):
        vals = [growth[y][kind] for y in YEARS]
        if any(v is None for v in vals):
            return None, None
        cum = math.prod(1 + v / 100 for v in vals) - 1
        base = 1 + cum
        cagr = (base ** (1 / len(YEARS)) - 1) if base > 0 else None
        return r(cum * 100, 1), r(cagr * 100, 1) if cagr is not None else None

    rev_cum, rev_cagr = cum_cagr("rev")
    eps_cum, eps_cagr = cum_cagr("eps")

    peg = info.get("pegRatio") or info.get("trailingPegRatio")
    peg_ok = None if peg is None else bool(0 < peg <= PEG_MAX)
    mcap = info.get("marketCap")

    next_earnings = None
    try:
        cal = tk.calendar
        ed = cal.get("Earnings Date") if isinstance(cal, dict) else None
        if ed:
            next_earnings = str(ed[0])
    except Exception:
        pass

    return {
        "name": info.get("shortName", ticker),
        "sektor": info.get("sector", "unbekannt"),
        "waehrung": info.get("currency", ""),
        "marktkapitalisierung": mcap,
        "mcap_ausreichend": None if mcap is None else bool(mcap >= MIN_MARKET_CAP),
        "kgv": r(info.get("trailingPE"), 1),
        "forward_kgv": r(info.get("forwardPE"), 1),
        "peg": r(peg, 2),
        "kgv_2026_2027_2028": "nicht verifiziert (yfinance liefert keine jahresgenauen KGV-Schaetzungen)",
        "kgv_hinweis": "Ursache eines niedrigen KGV (Zyklik, Einmaleffekte, Margen) manuell pruefen",
        "wachstum": {
            str(y): {"umsatz_pct": r(growth[y]["rev"], 1), "eps_pct": r(growth[y]["eps"], 1),
                     "quelle": quelle.get(y, "nicht verifiziert")}
            for y in YEARS
        },
        "wachstum_kumuliert_pct": {"umsatz": rev_cum, "eps": eps_cum},
        "wachstum_cagr_pct": {"umsatz": rev_cagr, "eps": eps_cagr},
        "fcf_wachstum": "nicht verifiziert",
        "ebitda_wachstum": "nicht verifiziert",
        "qualitaet": {
            "operative_marge_pct": r((info.get("operatingMargins") or 0) * 100, 1) if info.get("operatingMargins") is not None else None,
            "nettomarge_pct": r((info.get("profitMargins") or 0) * 100, 1) if info.get("profitMargins") is not None else None,
            "roe_pct": r((info.get("returnOnEquity") or 0) * 100, 1) if info.get("returnOnEquity") is not None else None,
            "verschuldung_debt_to_equity": r(info.get("debtToEquity"), 1),
            "cash": info.get("totalCash"),
            "free_cashflow_ttm": info.get("freeCashflow"),
            "organisch_vs_akquisition": "nicht verifiziert",
        },
        "naechste_quartalszahlen": next_earnings or "nicht verifiziert",
        "wkn": get_wkn(ticker, info),
        "kriterien_fundamental": {"kgv_angemessen": peg_ok, "wachstum_2026_2028": growth_ok},
    }


# ----------------------------------------------------------------------------
# Klassifizierung, Einstiegsanalyse, Endkontrolle
# ----------------------------------------------------------------------------
def classify(crit):
    missing = [k for k in AUTO_KEYS if crit.get(k) is not True]
    trend_base = all(crit.get(k) is True for k in TREND_BASE_KEYS)
    if not missing:
        return "A", missing
    if len(missing) <= 2:
        return "B", missing
    if trend_base:
        return "C", missing
    return None, missing


def entry_analysis(row):
    """Mechanisch berechnete Zonen - keine Anlageempfehlung."""
    price = row["preis"]
    ich = row["ichimoku"]
    levels = {"SMA 40": row["sma40"], "Kijun-sen": ich["kijun"], "Wolkenoberkante": ich["wolke_oben"]}
    below = {k: v for k, v in levels.items() if v is not None and v < price}
    entry_low = max(below.values()) if below else None
    struct = [v for v in (row["sma40"], ich["wolke_oben"]) if v is not None]
    stop = round(min(struct) * 0.99, 2) if struct else None
    target = row["hoch_60t"]
    rr = None
    if stop is not None and target is not None and price > stop and target > price:
        rr = round((target - price) / (price - stop), 2)
    return {
        "unterstuetzungszonen": levels,
        "sma40_stuetze": None if row["sma40"] is None else bool(price >= row["sma40"]),
        "kijun_stuetze": ich["kijun_als_stuetze"],
        "wolke_stuetze": ich["kurs_ueber_wolke"],
        "einstiegsspanne": None if entry_low is None else [round(entry_low, 2), price],
        "invalidierung_unter": stop,
        "invalidierung_regel": "Schlusskurs unter dem Niveau 1 % unter min(SMA 40, Wolkenoberkante)",
        "ziel_60t_hoch": target,
        "chance_risiko": rr,
        "hinweis": "Rein rechnerisch, keine Anlageempfehlung, kein Hebelprodukt.",
    }


def final_check(row):
    """Endkontrolle: prueft die Pflichtbedingungen fuer 'A' noch einmal direkt
    an den Rohwerten (unabhaengig von der Kriterien-Tabelle)."""
    ich = row["ichimoku"]
    checks = [
        row["rsl30"] is not None and row["rsl30"] > RSL_SHORT_MIN,
        row["rsl250"] is not None and row["rsl250"] > RSL_LONG_MIN,
        row["stoch_k"] is not None and row["stoch_k"] < STOCH_MAX,
        row["stoch_kreuz"] == "erfolgt",
        row["preis"] is not None and row["sma40"] is not None and row["preis"] > row["sma40"],
        row["kriterien"]["sma40_steigend"] is True,
        ich["kurs_ueber_wolke"] is True and ich["wolke_bullish"] is True and ich["tenkan_ueber_kijun"] is True,
        row["rsi14"] is not None and row["rsi14"] <= RSI_MAX,
        row["kriterien"]["kgv_angemessen"] is True,
        row["kriterien"]["wachstum_2026_2028"] is True,
    ]
    return all(checks)


def main():
    consensus = load_consensus()
    universe = {t: g for t, g in build_universe().items() if g in BENCHMARKS}
    tickers = sorted(universe)
    print(f"{len(tickers)} Ticker im Universum (ohne Nikkei/Rohstoffe)")

    bench = {}
    for group, (sym, name) in BENCHMARKS.items():
        h = load_history(sym)
        bench[group] = None if h is None else h["Close"]
        print(f"Benchmark {name}: {'ok, Stand ' + bench[group].index[-1].date().isoformat() if h is not None else 'NICHT verfuegbar'}")

    tech_rows = []
    for i, t in enumerate(tickers):
        row = analyze_technical(t, universe[t], bench[universe[t]])
        if row:
            tech_rows.append(row)
        if i % 25 == 0:
            print(f"Technik {i}/{len(tickers)}")
        time.sleep(0.2)

    if not tech_rows:
        latest = None
    else:
        latest = max(date.fromisoformat(x["datenstand"]) for x in tech_rows)
    tech_rows = [x for x in tech_rows
                 if latest and (latest - date.fromisoformat(x["datenstand"])).days <= STALE_DAYS
                 and x["liquide"] is not False]
    print(f"{len(tech_rows)} Titel mit ausreichenden, aktuellen Daten; neuester Handelstag {latest}")

    # Stufe 2 nur fuer Titel, die technisch (fast) passen oder den Trend intakt haben
    candidates = []
    for x in tech_rows:
        c = x["kriterien"]
        tech_fail = sum(1 for k in TECH_KEYS if c.get(k) is not True)
        trend_base = all(c.get(k) is True for k in TREND_BASE_KEYS)
        if tech_fail <= 2 or trend_base:
            candidates.append(x)
    print(f"{len(candidates)} Kandidaten fuer Fundamentalpruefung")

    results = []
    for j, x in enumerate(candidates):
        f = fetch_fundamentals(x["ticker"], consensus)
        if f["mcap_ausreichend"] is False:
            continue
        x.update({k: v for k, v in f.items() if k != "kriterien_fundamental"})
        x["kriterien"].update(f["kriterien_fundamental"])
        x["kriterien"]["keine_warnsignale"] = None     # nicht automatisch pruefbar
        klasse, missing = classify(x["kriterien"])
        if klasse is None:
            continue
        if klasse == "A" and not final_check(x):
            klasse = "B"                                 # Endkontrolle nicht bestanden
            missing = ["endkontrolle"]
        erfuellt = sum(1 for k, _ in CRITERIA if x["kriterien"].get(k) is True)
        x["klasse"] = klasse
        x["kriterien_erfuellt"] = f"{erfuellt}/{len(CRITERIA)}"
        x["kriterien_erfuellt_n"] = erfuellt
        x["fehlende_kriterien"] = [dict(CRITERIA).get(k, k) for k in missing]
        x["nicht_verifiziert"] = [dict(CRITERIA)[k] for k, _ in CRITERIA if x["kriterien"].get(k) is None]
        if klasse in ("A", "B"):
            x["einstieg"] = entry_analysis(x)
        results.append(x)
        if j % 10 == 0:
            print(f"Fundamentaldaten {j}/{len(candidates)}")
        time.sleep(0.3)

    results.sort(key=lambda z: (z["klasse"], -z["kriterien_erfuellt_n"]))
    counts = {k: sum(1 for z in results if z["klasse"] == k) for k in "ABC"}

    output = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "letzter_handelstag": latest.isoformat() if latest else None,
        "data_note": "Kurse ueber Yahoo-Finance-Gratis-API, ca. 15 Min. verzoegert. Keine Anlageberatung.",
        "universe_size": len(tickers),
        "analysiert": len(tech_rows),
        "rsl_methode": RSL_METHOD,
        "kriterien": [{"key": k, "name": n} for k, n in CRITERIA],
        "hinweis_klasse_a": (
            "Klasse A = alle automatisch pruefbaren Pflichtbedingungen erfuellt. "
            "'Keine gravierenden Warnsignale' (Gewinnwarnungen, Insiderverkaeufe, Guidance, "
            "Revisionen) muss weiterhin manuell geprueft werden. 2028-Wachstum ist nur mit "
            "consensus.csv verifizierbar."
        ),
        "anzahl": counts,
        "results": results,
    }

    def clean(obj):
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return None
        if isinstance(obj, dict):
            return {k: clean(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [clean(v) for v in obj]
        return obj

    with open("docs/trend_data.json", "w", encoding="utf-8") as fh:
        json.dump(clean(output), fh, ensure_ascii=False, indent=2)
    print(f"Fertig. Treffer: {counts}")


if __name__ == "__main__":
    main()
