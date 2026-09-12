"""
Three additions to the price-discovery result.

    python extension.py

Imports the machinery from Analysis.py unchanged -- same get(), align(), fit() --
so nothing about the headline number can move as a side effect of this file.

--------------------------------------------------------------------------
WHAT THIS ADDS, AND WHY EACH ONE IS A DIFFERENT KIND OF TEST
--------------------------------------------------------------------------
1. HIT RATE.  The slope says how much of the ETF move Tokyo repeats. It does
   not say how often the direction is right, which is the thing a trader asks
   first. A hit rate on its own is meaningless, so it is always printed next
   to the majority-class baseline -- if the Nikkei gaps up 55% of the time,
   a model that always says "up" scores 55% and has learned nothing.

2. OUT OF SAMPLE.  Sections 3-5 of Analysis.py fit and inspect the same data.
   That is in-sample diagnosis. This fits on early data, freezes the
   coefficients, and scores the later data it has never seen. Two versions: a
   single frozen split, and a walk-forward that refits as data arrives and
   never looks forward.

3. THE HOURS-SHUT LADDER.  The mechanism makes a prediction it cannot wriggle
   out of: the longer a market has been closed when New York shuts, the more
   information the ETF has had to absorb, so the more of the next open it
   should explain. Ten markets span 0 to 15.5 hours. If R^2 does not rise with
   hours shut, the Japan result is not price discovery and needs another name.
"""

import os

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from Analysis import (
    get, session_return, overnight_gap, intraday_return, align, fit, rule, FIGS, INK, ACCENT,
)


# ==========================================================================
# 1. Hit rate
# ==========================================================================

def hit_rate(x, y):
    """Fraction of sessions where the signal's sign matches the outcome's.

    Sessions where either value is exactly zero are dropped rather than
    scored: sign(0) is 0, which would count as a miss and quietly depress
    the number.

    'base' is what a constant forecast of the more common direction would
    score. The hit rate only means something above it.
    """
    x, y = np.asarray(x, float), np.asarray(y, float)
    keep = (x != 0) & (y != 0) & np.isfinite(x) & np.isfinite(y)
    x, y = x[keep], y[keep]
    if len(x) == 0:
        return {"hit": np.nan, "base": np.nan, "edge": np.nan, "n": 0}
    hit = float((np.sign(x) == np.sign(y)).mean())
    base = float(max((y > 0).mean(), (y < 0).mean()))
    return {"hit": hit, "base": base, "edge": hit - base, "n": int(len(x))}


def hit_by_magnitude(x, y, n=5, label=""):
    """Hit rate inside quantile buckets of |signal|.

    The average is the less interesting question. The real one is whether the
    signal works when it is LOUD. A relationship that is flat across buckets
    is a slope fitted through noise; one that strengthens with |signal| is
    behaving the way information should.
    """
    d = pd.DataFrame({"x": np.asarray(x, float), "y": np.asarray(y, float)}).dropna()
    d["bucket"] = pd.qcut(d["x"].abs(), n, labels=False, duplicates="drop")
    rows = []
    for b, g in d.groupby("bucket"):
        r = hit_rate(g["x"], g["y"])
        rows.append({"bucket": int(b) + 1,
                     "abs_signal_%": g["x"].abs().mean() * 100,
                     **r})
    out = pd.DataFrame(rows)
    if label:
        print(f"\n{label}")
        print(out.to_string(index=False,
                            formatters={"abs_signal_%": "{:.2f}".format,
                                        "hit": "{:.3f}".format,
                                        "base": "{:.3f}".format,
                                        "edge": "{:+.3f}".format}))
    return out


# ==========================================================================
# 2. Out of sample
# ==========================================================================

def oos_r2(actual, pred, benchmark):
    """Out-of-sample R^2 against a rival forecast, not against the test mean.

        R2_oos = 1 - SS(actual - pred) / SS(actual - benchmark)

    The benchmark is the training-period mean, which is the only forecast
    available at the time. Using the TEST mean instead -- what np.corrcoef
    would effectively give -- flatters the model by handing it knowledge of
    the period it is being scored on.

    This can be negative. A negative value is a real result: the fitted
    relationship did worse than predicting the historical average.
    """
    actual, pred, benchmark = (np.asarray(v, float) for v in (actual, pred, benchmark))
    ss_model = ((actual - pred) ** 2).sum()
    ss_bench = ((actual - benchmark) ** 2).sum()
    return float(1 - ss_model / ss_bench)


def split_test(df, cut, xcol="signal", ycol="gap"):
    """Fit once on everything before `cut`, freeze it, score everything after.

    The strictest version of the test: the coefficients never see the test
    period at all.
    """
    tr = df[df["us_date"] < cut]
    te = df[df["us_date"] >= cut]
    if len(tr) < 100 or len(te) < 100:
        raise ValueError(f"split at {cut} leaves {len(tr)}/{len(te)} obs")

    a = fit(tr[xcol], tr[ycol], f"  train  < {cut}")
    b = fit(te[xcol], te[ycol], f"  test  >= {cut}  (refit)")

    # fit() does not return the intercept, so refit it here on the TRAINING
    # data only. Same coefficients, nothing in Analysis.py has to change.
    slope, intercept = np.polyfit(tr[xcol].to_numpy(float), tr[ycol].to_numpy(float), 1)
    assert abs(slope - a["slope"]) < 1e-10, "train slope disagrees with fit()"
    pred = intercept + slope * te[xcol].to_numpy(float)
    r2 = oos_r2(te[ycol], pred, np.full(len(te), tr[ycol].mean()))
    h = hit_rate(te[xcol], te[ycol])
    print(f"  frozen train coefficients on the test period:  "
          f"R2_oos {r2:+.3f}   hit {h['hit']:.3f} vs base {h['base']:.3f}")
    return {"train": a, "test": b, "r2_oos": r2, "hit": h}


def walk_forward(df, xcol="signal", ycol="gap", min_train=500):
    """Refit on every session up to i, predict session i, step forward.

    Nothing is ever fitted on data at or after the session being predicted.
    This is the version that survives the 2024 regime shift: a single frozen
    split has to pick one relationship for a decade, while this one tracks a
    relationship that changes.
    """
    d = df.sort_values("us_date").reset_index(drop=True)
    x, y = d[xcol].to_numpy(float), d[ycol].to_numpy(float)
    if len(d) <= min_train:
        raise ValueError(f"{len(d)} obs is not enough for min_train={min_train}")

    pred, bench = np.empty(len(d) - min_train), np.empty(len(d) - min_train)
    for k, i in enumerate(range(min_train, len(d))):
        slope, intercept = np.polyfit(x[:i], y[:i], 1)
        pred[k] = intercept + slope * x[i]
        bench[k] = y[:i].mean()

    actual = y[min_train:]
    r2 = oos_r2(actual, pred, bench)
    h = hit_rate(pred, actual)
    print(f"  walk-forward from obs {min_train}:  {len(actual)} predictions   "
          f"R2_oos {r2:+.3f}   hit {h['hit']:.3f} vs base {h['base']:.3f}")
    return {"dates": d["us_date"].to_numpy()[min_train:], "pred": pred,
            "actual": actual, "r2_oos": r2, "hit": h}


# ==========================================================================
# 3. The hours-shut ladder
# ==========================================================================
#
# local close time, standard UTC offset. Hours shut is DERIVED from these
# rather than typed in, so the ladder cannot silently disagree with itself.
#
# New York closes at 16:00 ET = 21:00 UTC on standard time. Hours shut is the
# wall-clock distance from a market's close to that moment.
#
# These are standard-time offsets. DST moves several of them by an hour, and
# the southern hemisphere moves the opposite way, so treat every figure as
# +/- 1h. That is far below the spacing the test needs.

MARKETS = [
    # etf,   index,      country,     local close, UTC offset
    ("EWJ", "^N225",   "Japan",      15.5,  9.0),
    ("EWY", "^KS11",   "Korea",      15.5,  9.0),
    ("EWT", "^TWII",   "Taiwan",     13.5,  8.0),
    ("EWH", "^HSI",    "Hong Kong",  16.0,  8.0),
    ("INDA", "^NSEI",  "India",      15.5,  5.5),
    ("EWA", "^AXJO",   "Australia",  16.0, 10.0),
    ("EWU", "^FTSE",   "UK",         16.5,  0.0),
    ("EWG", "^GDAXI",  "Germany",    17.5,  1.0),
    ("EWC", "^GSPTSE", "Canada",     16.0, -5.0),
    ("EWZ", "^BVSP",   "Brazil",     17.0, -3.0),
]

NY_CLOSE_UTC = 21.0
NY_SESSION_HOURS = 6.5  # 09:30 to 16:00


def hours_shut(local_close, utc_offset):
    """Wall-clock hours between a market's close and New York's."""
    close_utc = local_close - utc_offset
    return (NY_CLOSE_UTC - close_utc) % 24


def ny_exposure(h):
    """Fraction of the New York session that ran AFTER the local close.

    THIS, NOT RAW HOURS SHUT, IS WHAT THE MECHANISM PREDICTS ON.

    The information the ETF absorbs arrives during New York's session and
    nowhere else. A market shut for 6.5 hours has already had the whole
    session to absorb; one shut for 15 hours has had exactly the same
    session, just with more dead time either side of it. So explanatory
    power should RISE up to the full session length and then FLAT-LINE.

    Regressing R^2 on raw hours_shut assumes it keeps climbing to 15.5. It
    does not, and a straight line through the whole range averages a real
    effect at the bottom together with a plateau at the top -- which makes
    the fit look better than the mechanism it is testing.
    """
    return min(h, NY_SESSION_HOURS) / NY_SESSION_HOURS


def market_table():
    """hours_shut is the regressor. concurrent_hours is a caveat, not a filter.

    A market shut for less than the 6.5-hour New York session was still trading
    for part of it. That does NOT disqualify it: the ETF's move after the local
    close is still a forecast of the next open, and hours_shut already measures
    how long that window was. What the overlap does is DILUTE the signal -- the
    concurrent portion of the ETF return tracks moves the local market has
    already priced into its own close, so it adds noise without adding
    information.

    So these markets stay in the ladder. Dropping them would be a serious
    mistake: without them hours_shut only spans 11 to 15.5, which is far too
    narrow a range to detect a slope. They are the low end of the x-axis and
    the test needs them.

    Canada is the one true control. hours_shut is exactly 0 -- the TSX closes
    at the same instant as New York, so there is no post-close window at all
    and no forecast is possible even in principle. Its R^2 is the zero point.
    """
    rows = []
    for etf, index, country, close, off in MARKETS:
        h = hours_shut(close, off)
        rows.append({
            "etf": etf, "index": index, "country": country, "hours_shut": h,
            "concurrent_hours": max(0.0, NY_SESSION_HOURS - h),
            "ny_exposure": ny_exposure(h),
            "diluted": h < NY_SESSION_HOURS,
        })
    return pd.DataFrame(rows).sort_values("hours_shut").reset_index(drop=True)


def stale_open_fraction(idx):
    """Fraction of sessions whose Open is EXACTLY the previous Close.

    THIS CHECK DECIDES WHETHER A MARKET CAN BE USED AT ALL. Some index feeds
    synthesise the open from the prior close instead of reporting it. Where
    that fraction is high the overnight gap is mostly zeros by construction,
    R^2 collapses to nothing, and the market looks like evidence against the
    mechanism when it is really evidence of a bad feed.

    Drop those markets and say so. Do not report them as low-R^2 rungs.
    """
    o, c = idx["Open"].to_numpy(float), idx["Close"].shift(1).to_numpy(float)
    m = np.isfinite(o) & np.isfinite(c)
    return float((o[m] == c[m]).mean())


def run_ladder(max_stale=0.05, min_obs=300):
    tab = market_table()
    print(tab.to_string(index=False, formatters={"hours_shut": "{:5.1f}".format}))
    print()

    out = []
    for r in tab.itertuples():
        try:
            etf, idx = get(r.etf), get(r.index)
            stale = stale_open_fraction(idx)
            targets = pd.concat([overnight_gap(idx), intraday_return(idx)], axis=1).dropna()
            d = align(session_return(etf), targets)

            if stale > max_stale:
                print(f"{r.country:11s} DROPPED -- {stale:.1%} of opens equal the prior "
                      f"close; the gap is not measured in this feed")
                continue
            if len(d) < min_obs:
                print(f"{r.country:11s} DROPPED -- only {len(d)} matched sessions")
                continue

            g = fit(d["signal"], d["gap"], quiet=True)
            h = hit_rate(d["signal"], d["gap"])
            print(f"{r.country:11s} {r.hours_shut:5.1f}h shut   slope {g['slope']:6.3f}   "
                  f"t {g['t']:6.1f}   R2 {g['r2']:.3f}   hit {h['hit']:.3f}/{h['base']:.3f}   "
                  f"n {g['n']:5d}   stale {stale:.1%}"
                  f"{f'   [{r.concurrent_hours:.1f}h concurrent]' if r.diluted else ''}")
            out.append({**r._asdict(), **g, "hit": h["hit"], "base": h["base"],
                        "stale": stale})
        except Exception as exc:
            print(f"{r.country:11s} FAILED  -- {type(exc).__name__}: {exc}")

    res = pd.DataFrame(out)
    if len(res) == 0:
        return res

    print()
    if len(res) >= 3:
        lin = fit(res["hours_shut"], res["r2"], f"R2 ~ raw hours shut ({len(res)})")
        exp = fit(res["ny_exposure"], res["r2"], f"R2 ~ NY exposure ({len(res)})")
        print("     Two shapes for the same claim. The saturating one is what the")
        print("     mechanism implies -- past 6.5 hours there is no more session")
        print("     left to absorb -- but SEVEN POINTS CANNOT SEPARATE THEM.")
        print("     Whichever fits better is not evidence for the functional form.")
        print(f"     linear-in-hours R2 {lin['r2']:.3f}  |  saturating R2 {exp['r2']:.3f}")

        # Canada is the only observation below 4.5 hours, so it anchors the
        # entire line. If the fit collapses without it, the honest claim is
        # "the zero-hour control is clean", not "power scales with hours".
        no_ctrl = res[res["hours_shut"] > 0]
        if len(no_ctrl) >= 3:
            fit(no_ctrl["hours_shut"], no_ctrl["r2"], "  leverage: drop the 0h control")

        # The plateau test: among markets that missed the ENTIRE NY session,
        # extra dead hours should buy nothing. A significant slope here would
        # mean something other than session absorption is driving the ladder.
        full = res[res["ny_exposure"] >= 1.0]
        if len(full) >= 3:
            print()
            pl = fit(full["hours_shut"], full["r2"],
                     f"  plateau test ({len(full)} fully-shut)")
            print(f"     Among markets that missed the whole session, extra hours buy")
            print(f"     {'SOMETHING -- investigate' if abs(pl['t']) > 2.5 else 'nothing, as predicted'}"
                  f" (t {pl['t']:.1f}). A flat line here is the result,")
            print("     not a failure: there is no more information left to absorb.")
            print("     But read it as CONSISTENT with saturation, not proof of it --")
            print("     five points spanning 11-15.5h could not detect a small slope.")

    if "Canada" in set(res["country"]):
        c = res.loc[res["country"] == "Canada"].iloc[0]
        verdict = ("CONSISTENT with the mechanism" if c["r2"] < 0.03
                   else "A PROBLEM -- the mechanism does not explain this")
        print(f"\n  -> Canada control (0.0h shut): R2 {c['r2']:.3f}, slope {c['slope']:+.3f}. "
              f"{verdict}")
    return res


# ==========================================================================
# Self-tests -- no internet required
# ==========================================================================

def run_tests():
    print("SELF-TESTS (no internet needed)")
    print("-" * 72)
    rng = np.random.default_rng(7)

    # -- hit rate ---------------------------------------------------------
    x = rng.normal(0, 1, 20_000)
    assert abs(hit_rate(x, x + rng.normal(0, .01, len(x)))["hit"] - 1.0) < .02
    print("PASS  hit rate ~1.00 when the outcome follows the signal")

    assert abs(hit_rate(x, rng.normal(0, 1, len(x)))["hit"] - 0.5) < .02
    print("PASS  hit rate ~0.50 when the outcome is independent")

    # Skewed outcome, no relationship: hit looks decent, edge exposes it.
    y = np.where(rng.random(len(x)) < .70, abs(rng.normal(0, 1, len(x))),
                 -abs(rng.normal(0, 1, len(x))))
    r = hit_rate(rng.normal(0, 1, len(x)), y)
    assert r["edge"] < 0.02 and r["base"] > 0.65, r
    print(f"PASS  skewed outcome: hit {r['hit']:.2f} but base {r['base']:.2f}, "
          f"edge {r['edge']:+.3f}")

    assert hit_rate([0, 0, 1], [0, 1, 1])["n"] == 1
    print("PASS  zero-valued sessions dropped, not scored as misses")

    # Loud signal should beat quiet signal when the noise is fixed.
    xs = rng.normal(0, 1, 40_000)
    ys = xs + rng.normal(0, 1.5, len(xs))
    hb = hit_by_magnitude(xs, ys, n=5)
    assert hb["hit"].iloc[-1] > hb["hit"].iloc[0] + .10, hb
    print(f"PASS  hit rate rises with |signal|: "
          f"{hb['hit'].iloc[0]:.2f} -> {hb['hit'].iloc[-1]:.2f}")

    # -- out-of-sample R^2 ------------------------------------------------
    a = rng.normal(0, 1, 500)
    assert oos_r2(a, a, np.full_like(a, a.mean())) > .999
    print("PASS  R2_oos ~1 for a perfect forecast")
    assert abs(oos_r2(a, np.full_like(a, a.mean()), np.full_like(a, a.mean()))) < 1e-9
    print("PASS  R2_oos ~0 when the model just predicts the benchmark")
    assert oos_r2(a, -a, np.full_like(a, a.mean())) < -1.0
    print("PASS  R2_oos negative for a forecast worse than the benchmark")

    # -- walk-forward on planted data -------------------------------------
    n = 1500
    d = pd.DataFrame({"us_date": pd.bdate_range("2015-01-01", periods=n)})
    d["signal"] = rng.normal(0, .01, n)
    d["gap"] = 0.6 * d["signal"] + rng.normal(0, .006, n)
    w = walk_forward(d, min_train=500)
    assert w["r2_oos"] > 0.15, w["r2_oos"]
    print(f"PASS  walk-forward finds planted 0.6 relationship, R2_oos {w['r2_oos']:+.3f}")

    d["gap"] = rng.normal(0, .006, n)  # relationship removed
    w0 = walk_forward(d, min_train=500)
    assert w0["r2_oos"] < 0.02, w0["r2_oos"]
    print(f"PASS  walk-forward finds nothing when there is nothing, "
          f"R2_oos {w0['r2_oos']:+.3f}")

    # -- hours shut -------------------------------------------------------
    t = market_table().set_index("country")
    assert t.loc["Canada", "hours_shut"] == 0.0
    assert t.loc["Japan", "hours_shut"] == 14.5
    assert t.loc["Taiwan", "hours_shut"] == 15.5
    print("PASS  hours shut: Canada 0.0, Japan 14.5, Taiwan 15.5")

    assert t.loc["Canada", "concurrent_hours"] == 6.5   # fully concurrent with NY
    assert t.loc["UK", "concurrent_hours"] == 2.0        # LSE shuts 11:30 ET
    assert t.loc["Japan", "concurrent_hours"] == 0.0
    print("PASS  concurrent hours: Canada 6.5, UK 2.0, Japan 0.0")

    assert ny_exposure(0.0) == 0.0
    assert abs(ny_exposure(4.5) - 4.5 / 6.5) < 1e-12
    assert ny_exposure(6.5) == 1.0 and ny_exposure(15.5) == 1.0
    print("PASS  NY exposure saturates at the full session: 0.0, 0.69, 1.0, 1.0")

    dil = set(t.index[t["diluted"]])
    assert dil == {"Canada", "Brazil", "UK", "Germany"}, dil
    print(f"PASS  dilution flag catches exactly {sorted(dil)} (kept, not dropped)")

    # rows 1 and 3 open exactly at the prior close; row 0 has no prior close.
    stale = pd.DataFrame({"Open": [1., 9., 2., 7.], "Close": [9., 5., 7., 1.]})
    assert abs(stale_open_fraction(stale) - 2 / 3) < 1e-12, stale_open_fraction(stale)
    print("PASS  stale-open detector counts exact Open == prior Close")
    print()


# ==========================================================================

def main():
    run_tests()
    os.makedirs(FIGS, exist_ok=True)   # Analysis.py does this in its own main()

    from Analysis import build
    df, ewj, n225 = build("EWJ", "^N225")

    rule("A. HIT RATE -- how often is the direction right?")
    for col, name in [("gap", "Tokyo opening gap"), ("intraday", "Tokyo intraday")]:
        r = hit_rate(df["signal"], df[col])
        print(f"{name:22s} hit {r['hit']:.3f}   base {r['base']:.3f}   "
              f"edge {r['edge']:+.3f}   n {r['n']}")
    hb = hit_by_magnitude(df["signal"], df["gap"], 5, "By |EWJ return| quintile (gap):")

    rule("B. OUT OF SAMPLE -- coefficients that never saw the test period")
    print("The regime shift in Analysis.py section 3 is in the INTRADAY slope,")
    print("not this one. The gap relationship is stable across the decade, so")
    print("frozen 2015-2020 coefficients still score on data they never saw.\n")
    split_test(df, "2021-01-01")
    print()
    walk_forward(df, min_train=500)

    rule("C. HOURS SHUT -- the mechanism's own prediction, across 10 markets")
    res = run_ladder()

    # ------------------------------------------------------------ figures
    rule("FIGURES")
    fig, ax = plt.subplots(figsize=(7, 4.5))
    if len(res):
        for flag, colour, lab in [(False, INK, "fully shut during NY session"),
                                  (True, ACCENT, "partly open during NY session")]:
            s = res[res["diluted"] == flag]
            ax.scatter(s["hours_shut"], s["r2"], s=70, color=colour, label=lab, zorder=3)
            for r in s.itertuples():
                ax.annotate(r.etf, (r.hours_shut, r.r2), textcoords="offset points",
                            xytext=(6, -3), fontsize=8, color=colour)
        if len(res) >= 3:
            b, a = np.polyfit(res["hours_shut"], res["r2"], 1)
            xs = np.linspace(0, 16, 50)
            ax.plot(xs, a + b * xs, color=INK, lw=1.0, ls=":", zorder=2,
                    label="linear in hours (wrong shape)")
        ax.axvline(NY_SESSION_HOURS, color="#888", lw=1.0, ls="--", zorder=1)
        ax.annotate("whole NY session\nabsorbed by here", (NY_SESSION_HOURS, 0.02),
                    xytext=(8, 0), textcoords="offset points", fontsize=7,
                    color="#666", va="bottom")
    ax.set_xlabel("hours the market had been shut when New York closed")
    ax.set_ylabel("R² of the next opening gap")
    ax.set_title("Explanatory power against time shut")
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(f"{FIGS}/ladder.png", dpi=150, bbox_inches="tight")
    print(f"  {FIGS}/ladder.png")

    fig, ax = plt.subplots(figsize=(6.5, 4))
    ax.bar(hb["bucket"], hb["hit"], color=INK, label="hit rate")
    ax.axhline(hb["base"].mean(), color=ACCENT, ls="--", lw=1.2,
               label="always-predict-majority baseline")
    ax.set_ylim(0.4, max(0.75, hb["hit"].max() + .05))
    ax.set_xlabel("|EWJ US-session return|, quintile")
    ax.set_ylabel("fraction of gaps with the right sign")
    ax.set_title("Does the signal work when it is loud?")
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(f"{FIGS}/hit_rate.png", dpi=150, bbox_inches="tight")
    print(f"  {FIGS}/hit_rate.png")

    rule("DONE")


if __name__ == "__main__":
    main()