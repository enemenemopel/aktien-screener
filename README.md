# aktien-screener

Zwei Screener laufen per GitHub Actions (Mo-Fr, 22:30 UTC) und schreiben nach `docs/`:

| Skript | Ausgabe | Dashboard | Idee |
|---|---|---|---|
| `screener.py` | `docs/data.json` | `docs/index.html` | stark gefallene Aktien mit niedrigem KGV |
| `trend_screener.py` | `docs/trend_data.json` | `docs/trend.html` | Trend-Pullback mit Einstiegssignal |

## Trend-Pullback-Screener

Universum: S&P 500, Nasdaq-100, S&P MidCap 400, DAX 40, EURO STOXX 50 (ohne Nikkei und Rohstoffe; US-Titel gegen den S&P 500 gerechnet). Alle Indikatoren werden aus den
Tageskursen selbst berechnet: SMA 40 (steigend), Ichimoku 9/26/52/26, RSI(14), Slow Stochastic 14/3/3
(bullisches Kreuz heute oder in den letzten 2 Handelstagen), RSL 30T > 1,05 und RSL 250T > 1,20 gegen
die Benchmark (S&P 500, DAX bzw. EURO STOXX 50). Alle Schwellen stehen oben in `trend_screener.py`.

Klassen: **A** = alle automatisch pruefbaren Kriterien erfuellt, **B** = maximal 2 fehlen,
**C** = Trend intakt, aber kein bestaetigtes Signal. Kriterien werden nicht gelockert; ohne Treffer bleibt
die Liste leer.

Wachstum: Nur das EPS-Wachstum zaehlt: 2027 > 25 % und 2028 > 8 % gegenueber dem Vorjahr, fuer 2026 gibt es keine Bedingung (Konstante `EPS_GROWTH_MIN`); 2028 darf fehlen (`GROWTH_2028_OPTIONAL`) und wird dann als "nicht verifiziert" ausgewiesen. Grenzen: yfinance liefert Konsensschaetzungen nur fuer das laufende und naechste Geschaeftsjahr. Fuer 2028
(und damit fuer Klasse A) wird eine optionale `consensus.csv` benoetigt mit den Spalten
`ticker,rev_2026,rev_2027,rev_2028,eps_2026,eps_2027,eps_2028` (Prozent gegenueber Vorjahr).
"Keine gravierenden Warnsignale" (Gewinnwarnungen, Insiderverkaeufe, Guidance) ist nicht automatisch
pruefbar und bleibt manuell. Keine Anlageberatung.

## Long-Score (0-100)

Jede Aktie in `trend.html` bekommt einen transparenten Score (Mauszeiger auf den Wert zeigt die Aufschluesselung):
Trend SMA 40 (10), Ichimoku (10), RSL 30T (8), RSL 250T (8), Korrektur (8), RSI (8), Slow Stochastic + Kreuz (14),
Bewertung PEG (8), EPS-Wachstum 2027/2028 (14), Qualitaet (12). Punkte sind gestuft (Mindestwert = 60 %, deutlich
darueber = voll). Nicht pruefbare Werte geben 0 Punkte und werden als "nicht verifiziert" ausgewiesen. Der Score
ersetzt die Pflichtbedingungen nicht: Klasse A/B/C bleibt massgeblich, Warnsignale werden nicht bewertet.
Gewichte und Stufen stehen in `SCORE_WEIGHTS` und `long_score()` in `trend_screener.py`.
