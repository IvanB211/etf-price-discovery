# Do US-listed ETFs price markets that are closed?

Measuring whether a US-listed Japan ETF forecasts the next Tokyo session, and
whether that relationship has been stable over the past decade.

**Headline:** it does — a slope of 0.61 on Tokyo's opening gap across 2,753
sessions, surviving two falsification tests. There is also residual
predictability *into* the Tokyo session, small historically but roughly five
times larger since 2024. A structural explanation for that change was tested and
rejected.

![main result](figures/main.png)

---

## The question

The Tokyo Stock Exchange closes at 15:30 JST — 02:30 in New York. EWJ, a
US-listed fund holding Japanese equities, then trades in New York from 09:30 to
16:00, roughly thirteen and a half hours after every share it holds last traded.

For that whole window the basket is frozen. Nothing in it is moving. So EWJ's
open-to-close return is **not** the basket changing value — it is traders
revising their estimate of what Japanese equities are worth, on information that
arrived after Tokyo shut.

That reframes the fund's price as a forecast, and the forecast is testable:
**does Tokyo open where EWJ said it would?**

The reframing also makes the project cheap. Because the basket's value is
constant over the window, the ETF's own return *is* the change in its premium to
net asset value — so no fund accounting data is needed, only free daily prices.

## Method

- **Signal** — EWJ's log return from the New York open to the New York close.
- **Targets** — the Nikkei 225's *opening gap* (open versus previous close) and
  its *intraday return* (open to close) in the next Tokyo session.
- **Sample** — 2015 to 2026, 2,753 matched sessions.

Splitting the target this way separates two questions. If the ETF has done real
price discovery, the information should appear in the **opening gap**. Anything
predictable in the **intraday** move means the open under-reacted and the market
carried on catching up during the day.

### The part that decides whether any of this is real

On any calendar date `d`, Tokyo's session for `d` ran from roughly 20:00 on
`d−1` to 02:30 on `d` New York time, while New York's session for `d` runs 09:30
to 16:00. **New York's day-`d` session therefore happens after Tokyo's.** The
session being forecast is the *next* one.

Matching New York on `d` to Tokyo on `d` looks backwards in time. Rather than
assume "`d+1`", the code looks up the next Tokyo session explicitly, so weekends
and each country's holidays are handled without assumption — necessary because
the two calendars are independent. A US holiday does not imply a Tokyo holiday,
and the tests pin exactly that case.

The tests run on fabricated data with a planted slope of 0.8. The correct
alignment recovers 0.81; the look-ahead version collapses to 0.09.

### One foreign session, one observation

An early version mapped several US sessions onto the same Tokyo session. Japan
has around sixteen public holidays a year, many on Mondays, and when Tokyo is
shut while New York trades, both the Friday and the Monday US session point
forward to the same Tokyo open. Those rows share a `y` value, so they are not
independent observations — which understates the standard errors and inflates
the t-statistics.

It affected **146 rows, exactly 5.0% of the sample**, and was invisible in the
summary statistics; it surfaced only when matched pairs were printed and read by
hand. The pipeline now keeps the *last* US session before each foreign session,
on the grounds that earlier information is already reflected in that session's
opening price, and the tests assert that no foreign session is ever used twice.

## Results

Full sample, 2,753 sessions, January 2015 to September 2026:

| | slope | std. error | t | R² |
|---|---|---|---|---|
| **Tokyo opening gap** | 0.612 | 0.020 | 30.4 | 0.251 |
| **Tokyo intraday** | 0.294 | 0.030 | 9.9 | 0.035 |

A 1% move in EWJ during the New York session is associated with the Nikkei
opening 0.61% higher, and EWJ's afternoon in New York explains about a quarter
of the variance in where Tokyo opens.

## Robustness: two attempts to kill it

**Placebo.** Shuffling the signal so it no longer lines up with the session it
supposedly predicts gives slopes of −0.019 (t = −0.8) and −0.001 (t = −0.0),
with R² below 0.001 in both cases. The matching procedure is not manufacturing
structure.

**Control.** Running the identical pipeline on SPY against the S&P 500 — where
no time-zone gap exists, so there is nothing to forecast:

| | slope | t | R² |
|---|---|---|---|
| gap | −0.029 | −2.5 | 0.002 |
| intraday | −0.110 | −5.7 | 0.011 |

Small, **negative**, and an order of magnitude below the Japanese result. The
sign matters: a pipeline fabricating a spurious positive relationship would
fabricate one here too. What appears instead is consistent with the known
short-horizon reversal in US equity indices — a different mechanism, in the
opposite direction.

Note also that the gap control is "significant" at t = −2.5 while explaining
0.2% of variance. With nearly 3,000 observations, trivial effects clear
significance thresholds automatically. Statistical significance and magnitude
are different questions.

## The relationship is not stable

| period | gap | intraday | intraday R² | n |
|---|---|---|---|---|
| 2015–2019 | 0.772 | 0.160 | 0.008 | 1,183 |
| 2020–2023 | 0.560 | 0.106 | 0.006 | 944 |
| 2024–2026 | 0.534 | **0.628** | **0.140** | 626 |

The gap coefficient declines gradually. The intraday coefficient does something
else entirely — flat and small for nine years, then roughly five times larger,
with a twenty-fold jump in explanatory power.

Information that used to arrive at Tokyo's open now arrives during the session.

![stability](figures/stability.png)

## A mechanism, tested and rejected

The Tokyo Stock Exchange extended its close from 15:00 to 15:30 and introduced a
closing auction on **5 November 2024** — inside the window where the change
appears. A closing auction concentrates end-of-day liquidity into one
price-forming event, which is a plausible reason for more information to be
absorbed within the session.

Splitting on the exact reform date rather than the calendar year:

| | intraday slope | t | n |
|---|---|---|---|
| pre-2024 baseline | 0.129 | 3.9 | 2,127 |
| Jan – 4 Nov 2024 (**before** reform) | 0.751 | 5.3 | 199 |
| 5 Nov 2024 onward (**after** reform) | 0.595 | 8.8 | 427 |

**Rejected.** The effect was already elevated — higher, in fact — before the
reform took effect. Whatever changed in 2024, it was not the closing auction.

The gap coefficient *does* fall across that date, from 0.743 to 0.472, in the
direction the mechanism predicts. But that rests on 199 observations, and by
this point roughly a dozen subsample regressions had been run; with enough
splits, one will look significant by chance. It is recorded here as something
worth further work, not as a second result.

## Sensitivity to extreme days

| recent sample (2024–2026) | intraday slope | R² | n |
|---|---|---|---|
| all days | 0.628 | 0.140 | 626 |
| top 1% by \|signal\| removed | 0.454 | 0.054 | 619 |
| ten largest days removed | 0.426 | 0.046 | 616 |

Removing **seven days out of 626** cuts the slope by 28% and R² by more than
half. The effect survives — 0.426 against a pre-2024 baseline of 0.129 is still
more than three times larger — but it is materially smaller than the headline
figure and materially dependent on a handful of extreme sessions. The August
2024 carry-trade unwind falls in this window.

## Currency

EWJ is priced in dollars, so part of its move is the yen rather than Japanese
equities. Correcting for this requires care in two places.

**The data.** Yahoo's daily FX `Open` is effectively that day's close — a third
of rows have `Open == Close` exactly, and the implied daily volatility is 0.02%
against a true USDJPY figure near 0.5%. The field looks entirely plausible and
is meaningless. Hourly `Close` values aligned to the 09:30–16:00 New York window
give a session volatility of **0.294%**, which is what USDJPY actually does.

**The sign.** `JPY=X` quotes yen per dollar, so the correction *adds* its return
rather than subtracting it. Verified on synthetic data, where the wrong sign
gives a correlation of 0.71 with the true equity return against 0.89 for making
no correction at all — worse than doing nothing.

On the two-year overlapping sample, both variants:

| | slope | t | R² |
|---|---|---|---|
| raw dollar returns | 0.528 | 14.7 | 0.250 |
| currency-adjusted | 0.523 | 15.8 | **0.278** |

The slope is unchanged while the fit improves. Simple attenuation would predict
the slope rising, so the yen is not behaving as pure measurement noise. The
likely reason: a weaker yen lifts Japanese exporters and so predicts a higher
Nikkei, while mechanically pushing a dollar-priced ETF *down*. Removing it gives
a cleaner equity signal but discards a term with predictive content of its own.

## Limitations

- **EWJ tracks MSCI Japan, not the Nikkei 225** — different constituents, and
  the Nikkei is price-weighted. Part of the shortfall from a slope of 1 is this
  basis rather than anything about markets. TOPIX would be a closer comparison.
- **Index opening prices may be stale.** The Nikkei's open is computed from 225
  constituents that do not all trade immediately. That alone would depress the
  gap coefficient and inflate the intraday one — the exact pattern observed.
  Distinguishing it requires intraday index data; if genuine under-reaction, the
  drift should spread through the session rather than concentrating in the first
  minutes.
- **Standard errors are ordinary OLS.** Returns cluster in volatility, so the
  true errors are wider. At t of 20–30 the conclusions are unaffected, but the
  figures are indicative.
- **Nothing here is tradeable.** Nikkei futures trade nearly around the clock on
  CME and Osaka's night session, so the overnight gap is already priced before
  Tokyo opens. This measures price discovery, not an opportunity.
- **Multiple testing.** Several subsample splits were run; the reported gap break
  at November 2024 should be treated accordingly.

## What I would do next

1. **Race the ETF against Nikkei futures.** The sharp question is whether EWJ
   carries information *beyond* what the overnight futures already show. A null
   result would be a strong result.
2. **Intraday index data** to separate genuine under-reaction from stale
   opening prices.
3. **Extend across markets** — Australia, Korea, Germany, the UK — and test
   whether explanatory power scales with the hours between local close and the
   New York close.
4. **Newey–West or bootstrapped standard errors.**

## Prior work

This phenomenon is documented. Antti Petajisto, *Inefficiencies in the Pricing
of Exchange-Traded Funds* (Financial Analysts Journal, 2017), finds ETF prices
fluctuating within a band of roughly 200 basis points around net asset value —
about 100 after controlling for stale pricing — with the largest deviations in
international and illiquid funds. This project is a replication on free data,
extended by the stability analysis and by testing a specific structural
hypothesis.

## Running it

```bash
pip install -r requirements.txt
python analysis.py
```

Self-tests run first and need no internet — if they pass, the logic is sound and
any later failure is a data or network problem. Market data is cached in
`data_cache/` on first run, so subsequent runs are offline and instant.

| file | |
|---|---|
| `analysis.py` | self-tests, data, analysis, figures — everything |
| `etf_step1.ipynb` | the exploratory notebook this grew out of |
