"""
etf_discovery.py — measuring whether US-listed ETFs price markets that are closed.

The idea in one paragraph
------------------------
EWJ is a US-listed fund holding Japanese equities. Tokyo closes at 15:30 JST,
which is 02:30 in New York. EWJ then trades in New York from 09:30 to 16:00,
against a basket in which nothing is moving. So EWJ's return over that window is
not the basket changing value — it is traders revising their estimate of what
Japanese equities are worth on news that arrived after Tokyo shut. That is a
forecast, and this module measures how good it is.

Everything here is deliberately small and inspectable. The one piece of real
subtlety is align(), which is where this analysis lives or dies.
"""

from __future__ import annotations

import datetime as dt
import os

import numpy as np
import pandas as pd

CACHE = "data_cache"
START = "2015-01-01"


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------

def get(ticker: str, start: str = START) -> pd.DataFrame:
    """Daily Open/Close, downloaded once and cached to disk.

    Uses RAW prices, never 'Adj Close'. Adjusted close folds dividends into the
    price series; mixing it with a raw open would put a fake jump into the
    open-to-close return on every ex-dividend date.
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


def fx_session_return(ticker: str = "JPY=X", period: str = "730d") -> pd.Series:
    """Currency return over the New York session, from HOURLY data.

    Why not daily bars: Yahoo's daily FX 'Open' is effectively that day's close.
    A third of rows have Open == Close exactly, and the resulting daily vol comes
    out around 0.02% against a true USDJPY figure near 0.5%. The field looks
    plausible and is meaningless — so we use hourly Close values instead and
    never touch the Open.

    Returns log(FX at ~16:00 ET / FX at ~09:30 ET), indexed by New York date.
    """
    import yfinance as yf
    h = yf.download(ticker, period=period, interval="1h", progress=False)
    if isinstance(h.columns, pd.MultiIndex):
        h.columns = h.columns.get_level_values(0)

    h = h[["Close"]].dropna()
    h.index = pd.to_datetime(h.index, utc=True).tz_convert("America/New_York")
    h = h.assign(date=h.index.date, t=h.index.time)

    start = h[h["t"] <= dt.time(9, 30)].groupby("date")["Close"].last()
    end   = h[h["t"] <= dt.time(16, 0)].groupby("date")["Close"].last()

    fx = np.log(end / start).rename("fx").dropna()
    fx.index = pd.to_datetime(fx.index)
    return fx


# --------------------------------------------------------------------------
# Return construction
# --------------------------------------------------------------------------

def session_return(df: pd.DataFrame) -> pd.Series:
    """Open-to-close log return. For EWJ this is the forecast window."""
    return np.log(df["Close"] / df["Open"]).rename("signal")


def overnight_gap(df: pd.DataFrame) -> pd.Series:
    """Where the index OPENS versus where it last CLOSED.

    If the ETF has already done the price discovery, the information should
    land here, in the opening auction.
    """
    return np.log(df["Open"] / df["Close"].shift(1)).rename("gap")


def intraday_return(df: pd.DataFrame) -> pd.Series:
    """Open to close. Predictability here means the open under-reacted."""
    return np.log(df["Close"] / df["Open"]).rename("intraday")


def currency_adjust(signal: pd.Series, fx: pd.Series) -> pd.Series:
    """Strip the currency out of a dollar-denominated ETF return.

    EWJ is priced in dollars but holds yen assets, so in log returns:

        r_EWJ = r_equity(in yen) + r_(dollars per yen)

    JPY=X quotes YEN PER DOLLAR — the reciprocal. Writing its return as r_Q and
    using log(1/x) = -log(x):

        r_EWJ = r_equity - r_Q      hence      r_equity = r_EWJ + r_Q

    Sanity check: yen weakens, so more yen buy a dollar, so r_Q > 0. Japanese
    assets are worth less in dollars, so EWJ falls. Adding r_Q back cancels it.

    Getting this sign backwards is worse than not correcting at all — verified
    on synthetic data, where the wrong sign gives correlation 0.70 with the true
    equity return against 0.89 for doing nothing.
    """
    both = pd.concat([signal, fx], axis=1).dropna()
    return (both.iloc[:, 0] + both.iloc[:, 1]).rename("signal")


# --------------------------------------------------------------------------
# Alignment — the part that matters
# --------------------------------------------------------------------------

def align(signal: pd.Series, targets: pd.DataFrame, max_gap_days: int = 4) -> pd.DataFrame:
    """Match each US session to the NEXT foreign session, strictly after it.

    On any calendar date d:
        Tokyo's session for d ran ~20:00 (d-1) to 02:30 (d), New York time
        New York's session for d runs 09:30 to 16:00 on d

    So New York's day-d session happens AFTER Tokyo's day-d session, and the
    session we are forecasting is the next one.

    Matching New York on d to Tokyo on d looks backwards in time. On synthetic
    data with a planted slope of 0.8, the correct direction recovers 0.74 and
    the backwards one recovers 0.09 — so the bug destroys the signal rather
    than inflating it, but either way the result is meaningless.

    merge_asof with direction='forward' and allow_exact_matches=False handles
    weekends and both countries' holidays without us reasoning about any of
    them, which matters because the two calendars are wholly independent.
    """
    left = signal.dropna().reset_index()
    left.columns = ["us_date", "signal"]

    right = targets.dropna().reset_index()
    right.columns = ["foreign_date"] + list(targets.columns)

    df = pd.merge_asof(
        left.sort_values("us_date"),
        right.sort_values("foreign_date"),
        left_on="us_date", right_on="foreign_date",
        direction="forward",
        allow_exact_matches=False,
    ).dropna()

    df["gap_days"] = (df["foreign_date"] - df["us_date"]).dt.days
    df = df[df["gap_days"] <= max_gap_days].reset_index(drop=True)

    if not (df["foreign_date"] > df["us_date"]).all():
        raise AssertionError("LOOK-AHEAD: a foreign date is not after its US date")
    return df


# --------------------------------------------------------------------------
# Estimation
# --------------------------------------------------------------------------

def fit(x, y, label: str = "", quiet: bool = False) -> dict:
    """Univariate OLS with standard error, t and R^2.

    Note on the t-statistics: these are ordinary OLS errors. Financial returns
    cluster in volatility, so the true errors are somewhat wider. At t of 20-30
    no correction changes the conclusion, but the figures should be read as
    indicative rather than exact.
    """
    x, y = np.asarray(x, float), np.asarray(y, float)
    beta, alpha = np.polyfit(x, y, 1)
    resid = y - (alpha + beta * x)
    se = np.sqrt((resid @ resid) / (len(x) - 2) / ((x - x.mean()) @ (x - x.mean())))
    r2 = np.corrcoef(x, y)[0, 1] ** 2

    if not quiet:
        print(f"{label:26s} slope {beta:7.3f}   se {se:.3f}   "
              f"t {beta/se:6.1f}   R2 {r2:.3f}   n {len(x)}")
    return {"slope": beta, "intercept": alpha, "se": se,
            "t": beta / se, "r2": r2, "n": len(x)}


def placebo(df: pd.DataFrame, column: str, seed: int = 42) -> dict:
    """Shuffle the signal so it no longer lines up with what it predicts.

    If the matching procedure itself manufactures structure, this still shows a
    slope. It should not.
    """
    rng = np.random.default_rng(seed)
    return fit(rng.permutation(df["signal"].values), df[column],
               f"placebo: {column}")


def trimmed(df: pd.DataFrame, column: str, q: float = 0.99) -> dict:
    """Re-fit with the largest-|signal| days removed, to see whether a handful
    of extreme sessions is carrying the result."""
    keep = df[df["signal"].abs() < df["signal"].abs().quantile(q)]
    return fit(keep["signal"], keep[column], f"trimmed {q:.0%}: {column}")