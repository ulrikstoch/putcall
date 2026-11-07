"""Faste, deterministiske krav. BESKYTTET FIL: research-agenterne må ikke ændre den.

En ændring her skal godkendes af et menneske i PR'en.
"""

from __future__ import annotations

# Research-gate: kører på hele historikken med den kapital, der faktisk skal handles.
RESEARCH = {
    "min_years": 8.0,  # mindst 8 års data
    "min_sharpe": 0.40,  # årlig Sharpe efter omkostninger
    "min_dsr": 0.90,  # Deflated Sharpe Ratio (korrigeret for antal forsøg)
    "max_dd": -0.35,  # største fald må ikke være værre end -35 %
    "min_positive_subperiods": 3,  # mindst 3 af 4 delperioder skal give plus
    "min_trades": 10,
    # Skal mindst være lige så god risikojusteret som at købe og holde SPY i samme periode
    "min_sharpe_vs_spy": 0.0,
}

# Forward-gate: fremadrettet papir-inkubation på data, der ikke fandtes ved registrering.
FORWARD = {
    "min_days": 60,  # handelsdage (ca. 3 måneder)
    "min_return": 0.0,  # skal give plus efter omkostninger
    "max_dd": -0.20,
    "min_sharpe": 0.0,
}

# Retire: en inkuberende kandidat pensioneres, hvis den falder mere end dette fremadrettet.
RETIRE_DD = -0.25

# Live-risiko
LIVE = {
    "kill_dd": -0.20,  # fald fra toppen af live-kontoværdien -> sælg alt, tilbagekald godkendelse
}


def research_gate(summary: dict, dsr: float, subperiods: list[float],
                  benchmark: dict | None = None) -> tuple[bool, list[str]]:
    g = RESEARCH
    fails = []
    if benchmark is not None and summary["sharpe"] < benchmark["sharpe"] + g["min_sharpe_vs_spy"]:
        fails.append(f"Sharpe {summary['sharpe']:.2f} er lavere end SPY køb-og-hold ({benchmark['sharpe']:.2f})")
    years = summary["days"] / 252
    if years < g["min_years"]:
        fails.append(f"for kort historik: {years:.1f} år < {g['min_years']}")
    if summary["sharpe"] < g["min_sharpe"]:
        fails.append(f"Sharpe {summary['sharpe']:.2f} < {g['min_sharpe']}")
    if dsr < g["min_dsr"]:
        fails.append(f"Deflated Sharpe {dsr:.2f} < {g['min_dsr']}")
    if summary["max_dd"] < g["max_dd"]:
        fails.append(f"max drawdown {summary['max_dd']:.0%} værre end {g['max_dd']:.0%}")
    pos = sum(1 for x in subperiods if x > 0)
    if pos < g["min_positive_subperiods"]:
        fails.append(f"kun {pos}/{len(subperiods)} delperioder i plus")
    if summary["trades"] < g["min_trades"]:
        fails.append(f"kun {summary['trades']} handler")
    return not fails, fails


def forward_gate(summary: dict) -> tuple[bool, list[str]]:
    g = FORWARD
    fails = []
    if summary["days"] < g["min_days"]:
        fails.append(f"inkubation {summary['days']} af {g['min_days']} handelsdage")
    if summary["total_return"] <= g["min_return"]:
        fails.append(f"afkast {summary['total_return']:+.1%} er ikke positivt")
    if summary["max_dd"] < g["max_dd"]:
        fails.append(f"max drawdown {summary['max_dd']:.0%} værre end {g['max_dd']:.0%}")
    if summary["sharpe"] <= g["min_sharpe"]:
        fails.append(f"Sharpe {summary['sharpe']:.2f} <= {g['min_sharpe']}")
    return not fails, fails
