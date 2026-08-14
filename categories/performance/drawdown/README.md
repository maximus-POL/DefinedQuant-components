# Drawdown

> Experimental Technical Preview. This component is draft, has author-supplied evidence, and has
> no domain review. Canonical inputs, outputs, units, defaults, and semantic ports live in
> `component.py`.

## Intuition

Drawdown measures how far each price sits below the highest price observed so far. A running high
is 0%; observations below it are negative. The underwater line makes the depth of a crash and the
subsequent path back toward the selected peak visible without confusing price direction with
one-period volatility.

## Formula

The component executes `Dₜ = Pₜ / max(P₀, …, Pₜ) − 1; MDD = minₜ Dₜ`. Equal highs replace the
running-peak index with the latest equal high. If multiple observations share the deepest
drawdown, the earliest trough is selected. For a negative episode, recovery is the first later
observation at or above that episode's peak value. When maximum drawdown is zero, the selected
peak and trough are the same earliest observation, and that observation is reported as recovered
immediately rather than requiring a later observation.

## Worked example

For prices `(100, 120, 90, 84, 108, 120)`, drawdowns are
`(0, 0, −0.25, −0.30, −0.10, 0)`. Maximum drawdown is `−0.30`, from the peak at index 1 to the
trough at index 3, with recovery at index 5.

## Visualization

The emitted underwater line contains every drawdown observation. Zero marks a running high and
negative values show the percentage depth below it. Timestamps become horizontal labels when
supplied; otherwise source-order indexes are displayed and chronology is marked unverified.

## Common mistakes

- Drawdown is not volatility: a smooth 30% decline can have low short-horizon volatility while
  still producing a deep drawdown.
- The result depends on the supplied start date. A peak before the first observation is invisible.
- Monthly data can miss an intramonth peak or trough that daily data would capture.

## Limitations

The component does not fetch, clean, sort, resample, align, or repair prices. It does not infer a
market calendar or calculate elapsed calendar/trading-day duration. Adjustment policy is accepted
from the caller. Tied episodes and the same-observation zero-drawdown recovery exception follow the
disclosed deterministic selection rules.
