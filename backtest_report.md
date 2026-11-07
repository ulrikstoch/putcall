# Backtest: XSP 0DTE opening range breakout

Kørt 2026-11-07 16:04 ET.

> **VIGTIGT: Optionspriserne er ESTIMATER.** Der findes ingen gratis historiske 0DTE-optionspriser,
> og IB leverer ikke historik for udløbne optioner. Priserne er beregnet med Black-Scholes
> med VIX × 1 som volatilitet, tid til udløb i handelsminutter, og en modelleret
> bid/ask-spread på max($0.02, 6% af mid). Køb antages fyldt på ask,
> salg på bid ved tærsklen. Virkelige 0DTE-priser (skew, IV-crush, gamma, spreads ved åbning)
> kan afvige betydeligt.

**Data:** Yahoo Finance: ^GSPC/10 som XSP (20 dage med 1-min bars, 10 ældre dage med 2-min bars), ^VIX intradag.

Handelsdage i data: 30. Sprunget over: intet signal: 1

> ⚠️ **Lille stikprøve (30 dage).** Resultaterne er statistisk usikre. Kør med
> `--source ib --days 500` mod IB Gateway for et langt bedre grundlag.

## Resultat pr. kontrakt (1 kontrakt pr. handel)

| Nøgletal | Værdi |
|---|---|
| Antal handler | 29 |
| Vinderprocent | 17.2% |
| Gns. gevinst | $246.20 (+81%) |
| Gns. tab | $-142.91 (-49%) |
| Samlet P/L (1 kontrakt) | $-2198.80 |
| Forventet værdi pr. handel | $-75.82 |
| Længste tabsstime | 9 handler i træk |
| Exit-årsager | stop loss: 22, take profit: 4, tidsstop: 3 |
| Gns. pris for én kontrakt | $291.89 (min $221.10, max $356.10) |
| Andel handler råd til med $150 | 0% |

## Alt-ind, kronologisk genafspilning fra $150

Slutsaldo: **$150.00** efter 29 signaldage.

| Dato | Kontrakter | Saldo efter | Note |
|---|---|---|---|
| 2026-09-28 | 0 | $150.00 | for dyr |
| 2026-09-29 | 0 | $150.00 | for dyr |
| 2026-09-30 | 0 | $150.00 | for dyr |
| 2026-10-01 | 0 | $150.00 | for dyr |
| 2026-10-02 | 0 | $150.00 | for dyr |
| 2026-10-05 | 0 | $150.00 | for dyr |
| 2026-10-06 | 0 | $150.00 | for dyr |
| 2026-10-07 | 0 | $150.00 | for dyr |
| 2026-10-08 | 0 | $150.00 | for dyr |
| 2026-10-09 | 0 | $150.00 | for dyr |
| 2026-10-12 | 0 | $150.00 | for dyr |
| 2026-10-13 | 0 | $150.00 | for dyr |
| 2026-10-14 | 0 | $150.00 | for dyr |
| 2026-10-15 | 0 | $150.00 | for dyr |
| 2026-10-16 | 0 | $150.00 | for dyr |
| 2026-10-19 | 0 | $150.00 | for dyr |
| 2026-10-20 | 0 | $150.00 | for dyr |
| 2026-10-21 | 0 | $150.00 | for dyr |
| 2026-10-22 | 0 | $150.00 | for dyr |
| 2026-10-23 | 0 | $150.00 | for dyr |
| 2026-10-26 | 0 | $150.00 | for dyr |
| 2026-10-27 | 0 | $150.00 | for dyr |
| 2026-10-28 | 0 | $150.00 | for dyr |
| 2026-10-29 | 0 | $150.00 | for dyr |
| 2026-10-30 | 0 | $150.00 | for dyr |
| 2026-11-02 | 0 | $150.00 | for dyr |
| 2026-11-03 | 0 | $150.00 | for dyr |
| 2026-11-04 | 0 | $150.00 | for dyr |
| 2026-11-05 | 0 | $150.00 | for dyr |

## Monte Carlo, alt-ind (10,000 kørsler, trækning med tilbagelægning af de 29 handelsudfald)

**Model A (som botten opfører sig):** dage hvor kontrakten er for dyr springes over.
Ruin = saldo under prisen på den billigste kontrakt i data ($221.10).

- Når $1450 før ruin: **0.0%**
- Ruin først: **100.0%**
- Uafgjort efter 2.000 handler: 0.0%

**Model B (streng):** ruin så snart dagens kontrakt koster mere end saldoen.

- Når målet: **0.0%**, ruin: **100.0%**

## Alle handler

| Dato | Type | Signal | XSP | Strike | VIX | Entry (ask) | Exit (bid) | Årsag | Exit kl. | P/L 1 kt. |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-09-28 | PUT | 10:38 | 769.50 | 769 | 16.1 | 2.61 | 1.26 | stop loss | 12:26 | -137.20 |
| 2026-09-29 | PUT | 11:02 | 767.30 | 767 | 16.0 | 2.59 | 1.25 | stop loss | 14:38 | -136.20 |
| 2026-09-30 | CALL | 10:00 | 771.18 | 772 | 15.6 | 2.66 | 1.29 | stop loss | 13:14 | -139.20 |
| 2026-10-01 | PUT | 10:00 | 765.17 | 765 | 17.4 | 3.20 | 1.55 | stop loss | 13:22 | -167.20 |
| 2026-10-02 | CALL | 10:26 | 775.27 | 776 | 15.5 | 2.58 | 1.25 | stop loss | 11:04 | -135.20 |
| 2026-10-05 | CALL | 10:06 | 774.17 | 775 | 15.7 | 2.65 | 3.50 | tidsstop | 15:00 | +82.80 |
| 2026-10-06 | CALL | 10:06 | 783.14 | 784 | 15.4 | 2.62 | 1.27 | stop loss | 13:12 | -137.20 |
| 2026-10-07 | PUT | 10:48 | 776.53 | 776 | 15.7 | 2.52 | 1.22 | stop loss | 11:46 | -132.20 |
| 2026-10-08 | CALL | 10:06 | 778.75 | 779 | 15.4 | 2.89 | 1.40 | stop loss | 12:42 | -151.20 |
| 2026-10-09 | CALL | 10:54 | 779.89 | 780 | 15.0 | 2.68 | 2.09 | tidsstop | 15:00 | -61.20 |
| 2026-10-12 | PUT | 10:07 | 780.78 | 780 | 15.3 | 2.51 | 1.21 | stop loss | 14:42 | -132.20 |
| 2026-10-13 | PUT | 11:25 | 779.27 | 779 | 15.3 | 2.42 | 1.17 | stop loss | 13:42 | -127.20 |
| 2026-10-14 | PUT | 10:06 | 775.54 | 775 | 15.9 | 2.72 | 5.25 | take profit | 14:00 | +250.80 |
| 2026-10-15 | CALL | 10:10 | 770.24 | 771 | 17.4 | 2.96 | 2.66 | tidsstop | 15:00 | -32.20 |
| 2026-10-16 | PUT | 10:10 | 773.36 | 773 | 16.1 | 2.81 | 5.45 | take profit | 13:03 | +261.80 |
| 2026-10-19 | PUT | 10:03 | 768.59 | 768 | 18.3 | 3.15 | 1.52 | stop loss | 11:07 | -165.20 |
| 2026-10-20 | CALL | 10:01 | 770.74 | 771 | 18.1 | 3.40 | 1.64 | stop loss | 14:38 | -178.20 |
| 2026-10-21 | PUT | 10:05 | 771.95 | 771 | 17.2 | 2.77 | 1.34 | stop loss | 11:27 | -145.20 |
| 2026-10-22 | CALL | 10:03 | 770.21 | 771 | 17.9 | 3.10 | 1.50 | stop loss | 10:26 | -162.20 |
| 2026-10-23 | PUT | 10:27 | 761.31 | 761 | 20.6 | 3.55 | 1.72 | stop loss | 10:52 | -185.20 |
| 2026-10-26 | CALL | 10:13 | 767.15 | 768 | 18.4 | 3.10 | 1.50 | stop loss | 14:40 | -162.20 |
| 2026-10-27 | PUT | 10:02 | 768.11 | 768 | 18.1 | 3.35 | 1.62 | stop loss | 10:59 | -175.20 |
| 2026-10-28 | CALL | 12:54 | 774.53 | 775 | 17.3 | 2.20 | 1.06 | stop loss | 14:05 | -116.20 |
| 2026-10-29 | CALL | 10:02 | 772.13 | 773 | 16.8 | 2.84 | 1.37 | stop loss | 13:09 | -149.20 |
| 2026-10-30 | PUT | 10:11 | 761.46 | 761 | 19.1 | 3.30 | 6.40 | take profit | 14:19 | +307.80 |
| 2026-11-02 | CALL | 10:11 | 763.18 | 764 | 18.5 | 3.15 | 1.52 | stop loss | 12:26 | -165.20 |
| 2026-11-03 | CALL | 10:40 | 757.30 | 758 | 20.5 | 3.35 | 1.62 | stop loss | 14:30 | -175.20 |
| 2026-11-04 | CALL | 10:15 | 770.72 | 771 | 16.9 | 3.10 | 1.50 | stop loss | 10:49 | -162.20 |
| 2026-11-05 | PUT | 10:04 | 754.74 | 754 | 21.0 | 3.55 | 6.85 | take profit | 10:26 | +327.80 |
