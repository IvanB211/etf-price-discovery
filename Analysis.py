"""
Do US-listed ETFs price markets that are closed?

Everything in one file: self-tests, data, analysis, figures.

    python analysis.py

The self-tests at the top need no internet. If they pass, the logic is sound
and any later failure is a data or network problem, not a bug.

--------------------------------------------------------------------------
THE IDEA
--------------------------------------------------------------------------
Tokyo closes at 15:30 JST, which is 02:30 in New York. EWJ, a US-listed fund
holding Japanese equities, then trades in New York from 09:30 to 16:00 against
a basket in which nothing is moving. So EWJ's return over that window is not the
basket changing value -- it is traders revising their estimate of what Japanese
equities are worth on news that arrived after Tokyo shut. That is a forecast,
and this measures how good it is.
"""

import datetime as dt
import os

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

CACHE, FIGS = "data_cache", "figures"
START = "2015-01-01"
REFORM = pd.Timestamp("2024-11-05")  # TSE extended close to 15:30 + closing auction
INK, ACCENT = "#26356B", "#A8620C"


# ==========================================================================
# Core logic
# ==========================================================================

def get(ticker, start=START):
    """Daily Open/Close, downloaded once then cached to disk.

    RAW prices, never 'Adj Close'. Adjusted close folds dividends into the
    price, which would put a fake jump into an open-to-close return on every
    ex-dividend date.
    """
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, ticker.replace("^", "").replace("=", "") + ".csv")

    if os.path.exists(path):
        df = pd.read_csv(path, index_col=0, parse_dates=True)
    else:
        import yfinance as yf
        df = yf.download(ticker, start=start, progress=False, auto_adjust=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if df.empty:
            raise RuntimeError(f"no data returned for {ticker}")
        df.to_csv(path)

    out = df[["Open", "Close"]].dropna()
    out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
    return out


def session_return(df):
    """Open-to-close log return. For EWJ this is the forecast window."""
    return np.log(df["Close"] / df["Open"]).rename("signal")


def overnight_gap(df):
    """Where the index OPENS vs where it last CLOSED. If the ETF has done the
    price discovery, the information should land here."""
    return np.log(df["Open"] / df["Close"].shift(1)).rename("gap")


def intraday_return(df):
    """Open to close. Predictability here means the open under-reacted."""
    return np.log(df["Close"] / df["Open"]).rename("intraday")


def currency_adjust(signal, fx):
    """Strip the currency out of a dollar-priced ETF holding yen assets.

        r_ETF = r_equity(yen) + r_(dollars per yen)

    JPY=X quotes YEN PER DOLLAR, the reciprocal. Writing its return r_Q and
    using log(1/x) = -log(x):

        r_ETF = r_equity - r_Q      so      r_equity = r_ETF + r_Q

    Check: yen weakens, so more yen buy a dollar, so r_Q > 0. Japanese assets
    are worth less in dollars, so the ETF falls. Adding r_Q back cancels it.

    The wrong sign is WORSE than no correction (see test_currency_sign).
    """
    both = pd.concat([signal, fx], axis=1).dropna()
    return (both.iloc[:, 0] + both.iloc[:, 1]).rename("signal")


def align(signal, targets, max_gap_days=4):
    """Match each US session to the NEXT foreign session, strictly after it.

    On date d, Tokyo's session ran ~20:00 (d-1) to 02:30 (d) New York time,
    while New York's runs 09:30 to 16:00 on d. So New York's day-d session
    happens AFTER Tokyo's, and the session being forecast is the next one.

    merge_asof(direction='forward', allow_exact_matches=False) handles weekends
    and both countries' holidays without assumption -- necessary because the
    calendars are independent. A US holiday does not imply a Tokyo holiday.
    """
    left = signal.dropna().reset_index()
    left.columns = ["us_date", "signal"]
    right = targets.dropna().reset_index()
    right.columns = ["foreign_date"] + list(targets.columns)

    df = pd.merge_asof(
        left.sort_values("us_date"), right.sort_values("foreign_date"),
        left_on="us_date", right_on="foreign_date",
        direction="forward", allow_exact_matches=False,
    ).dropna()

    df["gap_days"] = (df["foreign_date"] - df["us_date"]).dt.days
    df = df[df["gap_days"] <= max_gap_days]

    # Each foreign session must appear ONCE. When Tokyo is shut on a US trading
    # day (Japanese holidays, often Mondays), several US sessions map forward to
    # the same one; those rows share a y-value and are not independent, which
    # understates standard errors. Keep the LAST US session before it -- earlier
    # information is already in that session's opening price. ~5% of rows.
    df = (df.sort_values("us_date")
          .drop_duplicates("foreign_date", keep="last")
          .reset_index(drop=True))

    assert (df["foreign_date"] > df["us_date"]).all(), "LOOK-AHEAD BUG"
    assert not df["foreign_date"].duplicated().any(), "foreign session used twice"
    return df


def fit(x, y, label="", quiet=False):
    """Univariate OLS with standard error, t and R^2.

    These are ordinary OLS errors. Returns cluster in volatility so the true
    errors are wider; at t of 20-30 that changes nothing, but read the figures
    as indicative.
    """
    x, y = np.asarray(x, float), np.asarray(y, float)
    beta, alpha = np.polyfit(x, y, 1)
    resid = y - (alpha + beta * x)
    se = np.sqrt((resid @ resid) / (len(x) - 2) / ((x - x.mean()) @ (x - x.mean())))
    r2 = np.corrcoef(x, y)[0, 1] ** 2
    if not quiet:
        print(f"{label:26s} slope {beta:7.3f}   se {se:.3f}   "
              f"t {beta / se:6.1f}   R2 {r2:.3f}   n {len(x)}")
    return {"slope": beta, "se": se, "t": beta / se, "r2": r2, "n": len(x)}


# ==========================================================================
# Self-tests -- no internet required
# ==========================================================================

def _fixture(seed=0, true_beta=0.8):
    """Fabricated data with a KNOWN relationship, and Tokyo/New York shut on
    different days, because the calendars really are independent."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2024-01-01", "2024-03-29")
    tokyo = days.difference(pd.to_datetime(["2024-01-08", "2024-02-12", "2024-03-20"]))
    us = days.difference(pd.to_datetime(["2024-01-15", "2024-02-19"]))

    signal = pd.Series(rng.normal(0, 0.01, len(us)), index=us, name="signal")
    gap = {}
    for t in tokyo:
        prior = signal.index[signal.index < t]
        if len(prior):
            gap[t] = true_beta * signal.loc[prior[-1]] + rng.normal(0, 0.004)
    return signal, pd.DataFrame({"gap": pd.Series(gap)}), true_beta


def run_tests():
    print("SELF-TESTS (no internet needed)")
    print("-" * 72)
    signal, targets, true_beta = _fixture()
    df = align(signal, targets)

    def match(d):
        rows = df.loc[df["us_date"] == d, "foreign_date"]
        return str(rows.iloc[0].date()) if len(rows) else None

    assert (df["foreign_date"] > df["us_date"]).all()
    print("PASS  every foreign date is strictly after its US date")

    # Mon 8 Jan is a Tokyo holiday but a US trading day.
    assert match("2024-01-08") == "2024-01-09", match("2024-01-08")
    print("PASS  Tokyo holiday skipped:       2024-01-08 -> 2024-01-09")

    # Fri 5 Jan also points at Tokyo 9 Jan, so Monday supersedes it.
    assert match("2024-01-05") is None
    print("PASS  superseded session dropped:  2024-01-05 has no row")

    # Mon 15 Jan is a US holiday but Tokyo is OPEN. Hardcoding "next business
    # day" would get this wrong.
    assert match("2024-01-12") == "2024-01-15", match("2024-01-12")
    print("PASS  calendars independent:       2024-01-12 -> 2024-01-15")

    assert not df["foreign_date"].duplicated().any()
    print("PASS  each foreign session used exactly once")

    r = fit(df["signal"], df["gap"], quiet=True)
    assert abs(r["slope"] - true_beta) < 0.12, r["slope"]
    print(f"PASS  planted {true_beta} recovered as {r['slope']:.3f} ({r['n']} obs)")

    # The classic bug: matching to the SAME date, a session that already
    # happened. Confirms the test could detect it.
    L = signal.reset_index();
    L.columns = ["us_date", "signal"]
    R = targets.reset_index();
    R.columns = ["foreign_date", "gap"]
    bad = pd.merge_asof(L.sort_values("us_date"), R.sort_values("foreign_date"),
                        left_on="us_date", right_on="foreign_date",
                        direction="backward", allow_exact_matches=True).dropna()
    rb = fit(bad["signal"], bad["gap"], quiet=True)
    assert abs(rb["slope"]) < 0.3
    print(f"PASS  look-ahead version collapses to {rb['slope']:.3f}")

    # Currency sign.
    rng = np.random.default_rng(1)
    eq, q = rng.normal(0, .01, 50_000), rng.normal(0, .005, 50_000)
    idx = pd.date_range("2020-01-01", periods=len(eq), freq="h")
    rec = currency_adjust(pd.Series(eq - q, index=idx), pd.Series(q, index=idx))
    assert np.allclose(rec.values, eq)
    print("PASS  currency correction recovers the equity return exactly")
    print()


# ==========================================================================
# Analysis
# ==========================================================================

def build(etf_ticker, index_ticker):
    etf, idx = get(etf_ticker), get(index_ticker)
    targets = pd.concat([overnight_gap(idx), intraday_return(idx)], axis=1).dropna()
    return align(session_return(etf), targets), etf, idx


def rule(t):
    print(f"\n{'=' * 72}\n{t}\n{'=' * 72}")


def main():
    run_tests()
    os.makedirs(FIGS, exist_ok=True)

    rule("1. MAIN RESULT -- EWJ (New York) forecasting the Nikkei (Tokyo)")
    df, ewj, n225 = build("EWJ", "^N225")
    print(f"{len(df)} sessions, {df.us_date.min().date()} to {df.us_date.max().date()}\n")
    fit(df["signal"], df["gap"], "Tokyo opening gap")
    fit(df["signal"], df["intraday"], "Tokyo intraday")
    print("\nMatched pairs -- check a few against a calendar by hand:")
    print(df[["us_date", "foreign_date", "gap_days"]].head(6).to_string(index=False))

    rule("2. FALSIFICATION -- two attempts to show this is an artefact")
    print("(a) Placebo: same data, signal shuffled. Should be nothing.")
    rng = np.random.default_rng(42)
    shuffled = rng.permutation(df["signal"].values)
    fit(shuffled, df["gap"], "placebo: gap")
    fit(shuffled, df["intraday"], "placebo: intraday")

    print("\n(b) Control: SPY vs S&P 500. No time-zone gap, nothing to forecast.")
    print("    A spurious POSITIVE here would invalidate the method.")
    ctrl, _, _ = build("SPY", "^GSPC")
    fit(ctrl["signal"], ctrl["gap"], "SPY control: gap")
    fit(ctrl["signal"], ctrl["intraday"], "SPY control: intraday")

    rule("3. STABILITY -- is one number enough to describe a decade?")
    df["year"] = df["us_date"].dt.year
    for yrs, label in [(range(2015, 2020), "2015-2019"),
                       (range(2020, 2024), "2020-2023"),
                       (range(2024, 2027), "2024-2026")]:
        sub = df[df["year"].isin(yrs)]
        if len(sub) < 100:
            continue
        print(f"\n{label}  ({len(sub)} obs)")
        fit(sub["signal"], sub["gap"], "  gap")
        fit(sub["signal"], sub["intraday"], "  intraday")

    rule("4. TESTING A MECHANISM -- did the Nov 2024 TSE reform cause it?")
    print("TSE extended its close to 15:30 and added a closing auction on")
    print("5 Nov 2024. If that drove the change, the months BEFORE should")
    print("still look like history.\n")
    for label, sub in [
        ("pre-2024 baseline", df[df.us_date < "2024-01-01"]),
        ("Jan - Nov 4 2024 (BEFORE)", df[(df.us_date >= "2024-01-01") & (df.us_date < REFORM)]),
        ("Nov 5 2024 on  (AFTER)", df[df.us_date >= REFORM]),
    ]:
        print(f"\n{label}  ({len(sub)} obs)")
        fit(sub["signal"], sub["gap"], "  gap")
        fit(sub["signal"], sub["intraday"], "  intraday")

    rule("5. OUTLIERS -- is the recent effect a handful of days?")
    recent = df[df.us_date >= "2024-01-01"]
    fit(recent["signal"], recent["intraday"], "all recent days")
    keep = recent[recent["signal"].abs() < recent["signal"].abs().quantile(.99)]
    fit(keep["signal"], keep["intraday"], "top 1% removed")
    drop10 = recent.reindex(recent["signal"].abs().sort_values().index[:-10])
    fit(drop10["signal"], drop10["intraday"], "10 largest removed")

    rule("6. CURRENCY -- hourly FX, because the daily Open field is unusable")
    try:
        import yfinance as yf
        h = yf.download("JPY=X", period="730d", interval="1h", progress=False)
        if isinstance(h.columns, pd.MultiIndex):
            h.columns = h.columns.get_level_values(0)
        h = h[["Close"]].dropna()
        h.index = pd.to_datetime(h.index, utc=True).tz_convert("America/New_York")
        h = h.assign(date=h.index.date, t=h.index.time)
        s = h[h["t"] <= dt.time(9, 30)].groupby("date")["Close"].last()
        e = h[h["t"] <= dt.time(16, 0)].groupby("date")["Close"].last()
        fx = np.log(e / s).rename("fx").dropna()
        fx.index = pd.to_datetime(fx.index)

        print(f"session FX vol {fx.std() * 100:.3f}%   (sanity: should be ~0.2-0.3%,")
        print(f"                              not ~0.02% as the daily bars give)")

        targets = pd.concat([overnight_gap(n225), intraday_return(n225)], axis=1).dropna()
        raw = session_return(ewj)
        adj = currency_adjust(raw, fx)
        for name, sig in [("RAW (dollar returns)", raw[raw.index.isin(adj.index)]),
                          ("ADJ (currency-stripped)", adj)]:
            d = align(sig, targets)
            print(f"\n{name}  ({len(d)} obs)")
            fit(d["signal"], d["gap"], "  gap")
            fit(d["signal"], d["intraday"], "  intraday")
    except Exception as exc:
        print(f"skipped -- hourly FX unavailable: {exc}")

    # ---------------------------------------------------------------- figures
    rule("FIGURES")
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), sharex=True, sharey=True)
    for ax, col, title in zip(axes, ["gap", "intraday"],
                              ["Tokyo opening gap", "Tokyo session after the open"]):
        ax.scatter(df["signal"] * 100, df[col] * 100, s=6, alpha=.3,
                   edgecolors="none", color=INK)
        b, a = np.polyfit(df["signal"], df[col], 1)
        xs = np.linspace(df["signal"].min(), df["signal"].max(), 50)
        ax.plot(xs * 100, (a + b * xs) * 100, color=ACCENT, lw=2, label=f"slope {b:.2f}")
        ax.axhline(0, color="#bbb", lw=.6);
        ax.axvline(0, color="#bbb", lw=.6)
        ax.set_xlabel("EWJ return, US session (%)");
        ax.set_title(title)
        ax.legend(frameon=False)
    axes[0].set_ylabel("Nikkei return (%)")
    fig.tight_layout();
    fig.savefig(f"{FIGS}/main.png", dpi=150, bbox_inches="tight")
    print(f"  {FIGS}/main.png")

    w = 250
    roll = []
    for i in range(w, len(df) + 1):
        win = df.iloc[i - w:i]
        slope, _ = np.polyfit(win["signal"], win["intraday"], 1)
        roll.append((win["us_date"].iloc[-1], slope))
    rd = pd.DataFrame(roll, columns=["date", "slope"])
    plt.figure(figsize=(10, 4))
    plt.plot(rd["date"], rd["slope"], color=INK)
    plt.axhline(0, color="#bbb", lw=.6)
    plt.axvline(REFORM, color=ACCENT, ls="--", lw=1.2, label="TSE close reform")
    plt.ylabel("rolling intraday slope (250 sessions)")
    plt.title("The under-reaction is not stable over time")
    plt.legend(frameon=False);
    plt.tight_layout()
    plt.savefig(f"{FIGS}/stability.png", dpi=150, bbox_inches="tight")
    print(f"  {FIGS}/stability.png")

    rule("DONE")


if __name__ == "__main__":
    main()

