# putcall: XSP 0DTE opening range breakout-bot til Interactive Brokers

En fuldautomatisk bot, der handler 0DTE-optioner på XSP (mini-S&P 500) via IB Gateway
og kører uden opsyn på en Linux-VPS.

> ## ⚠️ Læs dette først
>
> **Backtesten (se `backtest_report.md`) viser, at strategien ikke kan køre med $150.**
> En XSP-option tæt på kursen (ATM) med udløb samme dag koster typisk **$220–360 pr. kontrakt**,
> når XSP står omkring 750–780. Med $150 kan botten derfor ikke købe én eneste kontrakt. Den
> springer dagen over med beskeden "ikke nok kontanter".
>
> Strategien gav også **negativ forventet værdi** i den (lille) testperiode: 17 % vindere, ca. −$76
> pr. handel. Optionspriserne i backtesten er Black-Scholes-estimater. Kør backtesten mod
> IB-data (`--source ib`) og lad botten køre på paper i flere uger, før der bruges rigtige penge.
> 0DTE-optioner kan miste hele værdien på få timer, og alt-ind betyder, at ét dårligt forløb
> kan tømme kontoen.

> **Nyt: `tradelab/`** er et strategi-laboratorium for aktier og ETF'er. Agenter forbedrer
> strategierne hver nat, de papirhandles og kan først gå live, når **du** godkender.
> Se [afsnittet om Tradelab](#tradelab-selvforbedrende-strategi-lab) nederst.

## Indhold

| Fil | Formål |
|---|---|
| `xspbot/config.py` | Al konfiguration i én dataclass. Hvert felt kan overskrives med en miljøvariabel med samme navn i STORE BOGSTAVER |
| `xspbot/engine.py` | Strategi, ordrer, exit-styring, overtagelse af positioner og STOP-fil |
| `xspbot/broker.py` | ib_async-integration (IB Gateway) |
| `xspbot/pricing.py` | Tick-afrunding, valg af strike, positionsstørrelse, filtre og exit-regler |
| `xspbot/state.py` | `state.json` med sidste handelsdato og flaget "mål nået" (atomisk skrivning) |
| `xspbot/notify.py` | Telegram. Fejl her kan aldrig vælte botten |
| `xspbot/calendar.py` | Tider i America/New_York og listen over halve handelsdage |
| `check_setup.py` | Tjek forbindelse, saldo, XSP-kurs og dagens optionskæde. Sender ingen ordrer |
| `backtest.py` | Backtest og Monte Carlo |
| `deploy/` | systemd-service og -timer, IB Gateway-service og et udsnit af IBC's config |
| `tests/` | pytest med mockede IB-objekter |

## Strategien kort

1. Opening range er højeste og laveste XSP-kurs fra 09:30 til 10:00 ET (1-min bars).
2. Fra 10:00 til 13:30 ET tjekkes kursen hvert sekund:
   - Kurs > high + 0,25 → køb CALL med første strike over kursen.
   - Kurs < low − 0,25 → køb PUT med første strike under kursen.
3. Filtre: mid ≥ $0,15 og spread ≤ 20 % af mid. Ellers springes dagen over.
4. Købet sendes som limitordre på ask, rundet op til gyldig tick ($0,01 under $3, ellers $0,05).
   Ordren annulleres efter 20 sek., hvis den ikke er fyldt.
   Antal kontrakter = `floor(kontant / (ask·100 + gebyr))`.
5. Exit vurderes hvert sekund på mid-prisen: +100 % (take profit), −50 % (stop loss) eller 15:00 ET (tidsstop).
   Salget sendes som limitordre på bid. Er den ikke fyldt efter 8 sek., annulleres den og sendes igen
   med ny bid, op til 8 gange. Derefter sendes en alarm, og botten bliver ved med at forsøge til
   markedet lukker. Er der ingen bid, sendes en advarsel.
6. Botten handler højst én gang om dagen. På halve handelsdage og dage uden XSP-udløb handler den ikke.
7. Når USD-saldoen når $1.450, sættes `done` i `state.json`, og botten handler ikke igen, før du
   nulstiller flaget: `python -m xspbot --reset-target`.

**Et signal pr. dag:** Hvis et signal springes over (filter, for lidt kontant eller købsordren ikke
fyldt), venter botten ikke på et nyt signal samme dag.

### Sikkerhed

- **Port 4002 (paper) er standard.** Port 4001 (live) afvises, medmindre miljøvariablen `IB_PORT=4001` er sat eksplicit.
- **`DRY_RUN=1`:** ingen ordrer. Alt andet kører, og simulerede fyld logges med `dry_run=1` i `trades.csv`.
- **Handelsdatoen gemmes i `state.json`, før købsordren sendes.** En genstart kan derfor ikke handle to gange samme dag. Bliver ordren slet ikke fyldt, fjernes datoen igen.
- **Ved opstart** annulleres efterladte XSP-ordrer. Findes der en åben XSP-optionsposition, overtages den med entry = avgCost/100 og styres til exit efter samme regler. Det gælder også, når målet er nået, eller STOP-filen findes.
- **Nødstop:** `touch STOP` i projektmappen. Åbne positioner lukkes, og der åbnes ingen nye. Slet filen for at fortsætte.
- **Uventede fejl** logges og sendes til Telegram. Botten genforbinder hvert 15. sek. indtil 16:00 ET og overtager positionen igen. Giver den op, sendes en kritisk alarm.

## Logning

- `bot.log` er den fulde log (roteres ved 10 MB).
- `trades.csv` har kolonnerne `dato, kontrakt, antal, entry, exit, aarsag, pl_usd, dry_run`. P/L er inkl. det estimerede gebyr på både køb og salg.
- Telegram får besked ved opening range, signal, køb, salg, overspringning, fejl og "mål nået".

---

## 1. IBKR-opsætning (gøres manuelt)

1. **Cash-konto:** Opret eller konvertér til en *Cash account* (ikke Margin) under
   *Settings → Account Settings → Account Type*.
2. **Optionstilladelse:** *Settings → Trading Permissions → Options*. Køb af calls og puts kræver
   mindst det niveau, der tillader "long calls/puts" (hos IBKR typisk niveau 2). Det kræver, at du
   angiver handelserfaring.
3. **Markedsdata:** under *Settings → Market Data Subscriptions*. Abonnér på:
   - **OPRA (US Options Exchanges)** for optionskurser.
   - **Cboe-indeksdata** (fx *Cboe Streaming Market Indexes*) for XSP-indeksets kurs.

   Navne og priser ændres af IBKR. Kør `check_setup.py`, der viser, om du får kurser.
4. **Del data med paper:** *Settings → Account Settings → Paper Trading Account → Share real-time
   market data subscriptions with paper trading account = Yes*. Ellers får paper-kontoen ingen live-data.
   Bemærk, at IBKR kun leverer live-data til én session ad gangen pr. bruger. Er du logget ind i
   TWS eller mobilappen med live-brugeren, kan paper-gatewayen miste data.
5. **API i IB Gateway:** *Configure → Settings → API → Settings*:
   - "Enable ActiveX and Socket Clients" slået til.
   - "Read-Only API" slået **fra**.
   - Port 4002 for paper og 4001 for live.
   - "Trusted IPs": 127.0.0.1.

   IBC kan sætte de fleste af disse værdier.

### Veksling DKK → USD

Botten bruger kun **USD-kontant** (`CashBalance` i USD, begrænset af `AvailableFunds`).

- Indsæt DKK og veksl i Client Portal: *Transfer & Pay → Convert Currency*, vælg DKK → USD.
- Du kan også handle `USD.DKK` i TWS: køb USD for DKK med ordretypen "Cash quantity".
- Alternativt kan du overføre USD direkte, fx via Wise, og undgå IBKR's minimumsgebyr på små vekslinger.
- Tjek bagefter med `check_setup.py`, at "Disponibel USD" viser beløbet.

På en cash-konto afregnes optionshandler T+1. Med én handel om dagen er kontanten afregnet næste morgen.

## 2. Installation på Ubuntu-VPS

```bash
sudo adduser --disabled-password trader
sudo apt update && sudo apt install -y python3 python3-venv git xvfb unzip openjdk-17-jre-headless
sudo -iu trader
git clone https://github.com/ulrikstoch/putcall.git && cd putcall
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env      # udfyld Telegram, behold IB_PORT=4002 og DRY_RUN=1 i starten
.venv/bin/python -m pytest -q           # alle tests skal være grønne
```

Python 3.11 eller nyere kræves (`zoneinfo` og `tzdata` er med).

### Telegram

1. Skriv til `@BotFather` → `/newbot` → kopiér token til `TELEGRAM_TOKEN`.
2. Send en besked til din nye bot. Åbn `https://api.telegram.org/bot<TOKEN>/getUpdates` og kopiér
   `chat.id` til `TELEGRAM_CHAT_ID`.
3. Test: `.venv/bin/python check_setup.py --telegram`.

### IB Gateway + IBC (automatisk login)

1. Installér IB Gateway (stable, Linux) som brugeren `trader`:
   ```bash
   wget https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-x64.sh
   chmod +x ibgateway-*.sh && ./ibgateway-stable-standalone-linux-x64.sh -q
   ```
   Notér versionsnummeret i mappen `~/Jts/ibgateway/<version>`.
2. Hent IBC fra <https://github.com/IbcAlpha/IBC/releases> (IBCLinux-zip), og pak den ud i `/opt/ibc`.
   Kør `chmod +x /opt/ibc/*.sh /opt/ibc/scripts/*.sh`.
3. Kopiér `/opt/ibc/config.ini` til `~/ibc/config.ini`, og udfyld den efter
   `deploy/ibc-config.ini.example`: brugernavn, password, `TradingMode=paper`, `ReadOnlyApi=no`
   og `OverrideTwsApiPort=4002`. Kør `chmod 600 ~/ibc/config.ini`.
4. I `/opt/ibc/gatewaystart.sh` sættes `TWS_MAJOR_VRSN`, `IBC_INI=~/ibc/config.ini` og
   `TRADING_MODE=paper`.
5. Installér servicen:
   ```bash
   sudo cp deploy/ibgateway.service /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now ibgateway
   ```
6. **Live og 2FA:** Live-login kræver godkendelse i IBKR Mobile. IBC venter og prøver igen.
   IBKR kræver fuld genlogin mindst én gang om ugen (typisk søndag), og den skal du godkende på
   telefonen. Gatewayen genstarter dagligt via `AutoRestartTime`, som angives i VPS'ens lokale tid.

### Botten som systemd-timer (09:15 ET, mandag–fredag)

```bash
sudo cp deploy/xspbot.service deploy/xspbot.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now xspbot.timer
systemctl list-timers xspbot.timer          # viser næste start i din lokale tid
journalctl -u xspbot -f                     # følg dagens kørsel
```

Timeren bruger `OnCalendar=Mon..Fri *-*-* 09:15:00 America/New_York`, så sommer- og vintertid
håndteres af systemd, uanset VPS'ens tidszone. Det kræver systemd 235 eller nyere (Ubuntu 20.04+).
Botten kører, til dagens handel er afsluttet (senest omkring 15:00–16:00 ET), og afslutter derefter.
På helligdage finder den intet udløb og stopper.

Halve handelsdage er hardcodet i `xspbot/calendar.py` (2026-11-27, 2026-12-24, 2027-11-26 og
2028-datoerne). Opdatér listen hvert år fra NYSE's kalender. Botten advarer, hvis listen ikke dækker
indeværende år.

### Daglig drift

```bash
touch ~/putcall/STOP                 # nødstop: luk positioner, ingen nye handler
rm ~/putcall/STOP                    # genoptag
.venv/bin/python -m xspbot --status  # vis state.json
.venv/bin/python -m xspbot --reset-target   # nulstil "mål nået"
```

## 3. Verificér opsætningen

```bash
.venv/bin/python check_setup.py            # live-data
.venv/bin/python check_setup.py --delayed  # hvis du endnu ikke har abonnement
```

Scriptet viser konti, USD-saldo, åbne XSP-positioner, XSP-kursen og de næste udløb. For dagens udløb
viser det bid/ask for de to strikes, botten ville vælge, og hvor mange kontrakter din saldo rækker
til. Det sender ingen ordrer.

## 4. Fra paper til live

1. Lad botten køre på **paper med `DRY_RUN=1`** i nogle dage, og derefter med `DRY_RUN=0`.
   Gennemgå `bot.log`, `trades.csv` og Telegram-beskederne.
2. Sæt i IBC's config `TradingMode=live` og `OverrideTwsApiPort=4001`, og sæt `TRADING_MODE=live`
   i `gatewaystart.sh`. Kør derefter `sudo systemctl restart ibgateway`, og godkend 2FA på telefonen.
3. I `.env`: `IB_PORT=4001`. Det skal stå eksplicit, ellers nægter botten at starte. Brug
   `DRY_RUN=1` den første live-dag.
4. Kør `check_setup.py`, og kontrollér, at der står **LIVE**, og at saldoen er rigtig.
5. Sæt `DRY_RUN=0`. Botten starter selv kl. 09:15 ET næste handelsdag.

Gå tilbage til paper ved at sætte `IB_PORT=4002` og `TradingMode=paper`.

## 5. Backtest

```bash
.venv/bin/python backtest.py                          # Yahoo: kun ca. 30 dage intradag-data
.venv/bin/python backtest.py --source ib --days 500   # 1-min XSP og VIX fra IB Gateway (anbefalet)
.venv/bin/python backtest.py --source csv --index-csv spx_1min.csv --vix-csv vix.csv
.venv/bin/python backtest.py --vol-mult 0.8 --spread-pct 0.04   # følsomhed
```

Rapporten skrives til `backtest_report.md`, og handlerne til `backtest_report.csv`.

**Optionspriserne er estimater.** Der findes ingen gratis historiske 0DTE-optionspriser, og IBKR
leverer ikke historik for udløbne optioner. Priserne beregnes derfor med Black-Scholes med VIX som
volatilitet plus en modelleret spread. Køb antages fyldt på ask og salg på bid ved tærsklen. Har bar
både stop- og gevinstniveauet, antages stop loss først.

Monte Carlo trækker 10.000 gange med tilbagelægning af de historiske handelsudfald og bruger
alt-ind-reglen. To definitioner rapporteres:

- **A:** for dyre dage springes over. Ruin = saldoen er under prisen på den billigste kontrakt i data.
- **B (streng):** ruin, så snart dagens kontrakt ikke kan betales.

## Tests

```bash
.venv/bin/python -m pytest -q
```

Testene dækker tick-afrunding, valg af strike, positionsstørrelse, filtre, exit-logik,
`state.json`, konfiguration (live-spærre) og IB-wrapperen mod mockede ib_async-objekter. Med en
falsk broker og et falsk ur dækker de også hele handelsdage: take profit, stop loss, tidsstop,
salgsforsøg, ingen bid, delvise fyld, overtagelse af åben position, genstart efter nedbrud midt i
en handel, STOP-filen, mål nået og DRY_RUN.

---

# Tradelab: selvforbedrende strategi-lab

Tradelab er bygget til at finde strategier, der tjener penge efter omkostninger på en lille
konto. Det er bygget, så agenterne har svært ved at narre sig selv eller dig.

```
 Claude-agent (hver nat)          VPS (hver handelsdag)                 Dig
 ───────────────────────          ─────────────────────                 ───
 hypotese -> backtest  ─┐
 (alle forsøg logges)   │
                        ▼
              RESEARCH-GATE  (8+ år, Deflated Sharpe, ≥ SPY risikojusteret,
                        │     3/4 delperioder i plus, maxDD > −35 %, ingen look-ahead)
                        ▼
              INKUBATION  (koden låses via hash; fremadrettet test på nye data;
                        │   den bedste kandidat papirhandles på IBKR kl. 09:45 ET)
                        ▼
              FORWARD-GATE  (≥ 60 handelsdage, plus, Sharpe > 0, maxDD > −20 %)
                        │
                        ▼   status "ready" -> PR + Telegram
                                                          approve ID --capital N
                                                          (kun i terminal på VPS)
                                                                    │
                                                                    ▼
                                                     LIVE med loft over beløbet og
                                                     nødstop ved −20 % fra toppen
```

## Vigtige værn

- **Alle backtests logges** i `lab/trials.jsonl`. Kravet (Deflated Sharpe) bliver strengere,
  jo flere varianter der er prøvet. Så kan heldige fund ikke slippe igennem.
- **Strategien skal mindst være lige så god som at købe og holde SPY**, når der justeres for
  risiko. Ellers er den ikke besværet værd.
- **Koden låses ved registrering.** Kandidatens id er en hash af strategiens kildekode,
  `indicators.py` og parametrene. Ændres noget, pensioneres kandidaten, og en ny skal
  inkubere fra start. En godkendelse til live gælder kun præcis den hash.
- **Look-ahead tjekkes automatisk** ved hver backtest.
- **Realistiske omkostninger** efter IBKR's tiered-takster: min. $0,35 pr. ordre, 5 bps
  slippage og handel på næste dags åbning.
- **Agenterne kan aldrig sætte noget live.** `approve` kræver en interaktiv terminal på
  VPS'en, og godkendelsen ligger i den lokale `approvals.json`, som ikke er i git.
- **Papir-tilstand nægter at handle på en rigtig konto**, og live-tilstand nægter at handle
  på en papirkonto. Kontoen genkendes på, om id'et starter med "DU".
- **Nødstop i live:** falder kontoværdien 20 % fra toppen, sælges alt, godkendelsen
  tilbagekaldes, og du får besked.
- **Agenternes ændringer kommer som PR'er.** VPS'en kører kun `main`, så intet ændrer sig
  på VPS'en, før du har merget.

## Kommandoer

```bash
python -m tradelab list                               # strategier
python -m tradelab backtest TrendSMA_v1 --params '{"n": 150}'
python -m tradelab register KEY --params '{...}' --note "hypotese"
python -m tradelab evaluate                           # forward-status -> lab/STATUS.md
python -m tradelab status                             # inkl. aktiv live-godkendelse
python -m tradelab trade --mode paper                 # rebalancér papirkontoen nu
python -m tradelab approve <ID> --capital 150         # DIG: godkend til live
python -m tradelab revoke                             # DIG: stop live
```

## Opsætning af papirhandel på VPS'en

1. Følg IBKR- og VPS-trinnene ovenfor. Til aktier og ETF'er skal du desuden have:
   - handelstilladelse til **US Stocks**
   - **brøkdele** slået til: *Settings → Account Settings → Fractional Share Trading*
   - markedsdata for amerikanske aktier, fx *US Securities Snapshot and Futures Value Bundle*
     eller *NASDAQ/NYSE*
2. **Kør ikke XSP-botten og tradelab på den samme papirkonto.** XSP-botten bruger al
   kontant. Slå den fra med `sudo systemctl disable --now xspbot.timer`.
3. Aktivér papirhandlen:
   ```bash
   sudo cp deploy/tradelab-paper.* /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now tradelab-paper.timer
   ```
   Hver handelsdag kl. 09:45 ET henter VPS'en den seneste `main`, rebalancerer papirkontoen
   efter den bedste kandidat og sender status til Telegram. Faktiske fyld logges i
   `lab_fills.csv`, så du kan sammenligne med modellen.

## Når en kandidat er "ready": sådan går du live

1. Læs PR'en og `lab/STATUS.md`. Spørg dig selv, om hypotesen giver mening.
2. Opret en separat `.env.live` med `IB_PORT=4001` og evt. `IB_ACCOUNT`, og start en
   live-gateway. Det kan være en anden IBC-instans med `TradingMode=live`.
3. Kør på VPS'en:
   ```bash
   python -m tradelab approve <ID> --capital 150
   ```
   Du skal skrive id'et for at bekræfte.
4. Aktivér live-timeren:
   ```bash
   sudo cp deploy/tradelab-live.* /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now tradelab-live.timer
   ```
   Live-servicen kører `git pull` med vilje ikke. Live-koden ændrer sig kun, når du selv
   opdaterer den.
5. **Udbetaling:** hæv fra IBKR (*Transfer & Pay → Withdraw*) til din danske bankkonto.
   Bagefter skal toppunktet nulstilles, ellers ligner udbetalingen et tab og udløser
   nødstoppet: slet `lab_live_state.json`.

Skat: gevinster og udbytter fra amerikanske ETF'er skal selvangives i Danmark.
Aktiebaserede ETF'er beskattes ofte som kapitalindkomst efter lagerprincippet, hvis de står på
SKAT's liste over aktiebaserede investeringsselskaber. Undersøg det for de konkrete ETF'er, eller
spørg en rådgiver.

## Den natlige agent

En planlagt Claude Code-session kører hver hverdag efter det amerikanske marked er lukket.
Den følger [`lab/AGENT.md`](lab/AGENT.md):

1. evaluerer kandidaterne
2. skriver hypoteser i `lab/journal.md`
3. tester højst 10 varianter og registrerer højst én
4. skriver en rapport i `lab/reports/`
5. opdaterer én PR mod `main`

**Du skal kun gøre to ting:**

- merge PR'en, når du er enig. Det starter papirhandel af nye kandidater på VPS'en.
- køre `approve`, hvis en kandidat bliver "ready", og du vil gå live.

**Forvent, at der går mindst 3 måneder, før noget kan blive "ready".** Det kan også ske, at
intet nogensinde bliver det. Så har systemet sparet dig for at tabe penge.
