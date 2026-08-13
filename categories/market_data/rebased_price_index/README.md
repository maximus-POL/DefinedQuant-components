# Rebased Price Index

> Experimental Technical Preview. This component is draft, has author-supplied evidence, and has
> no domain review. Canonical inputs, outputs, units, defaults, and semantic ports live in
> `component.py`.

## Intuition

Rebasing converts a positive price path into a unitless index without changing its shape. The
caller selects both the base observation and its displayed level, so no hidden “start at 100”
convention affects the result. A rising line means the supplied price is above its base-relative
level; a falling line means it is below it.

## Formula

For base index `b`, base value `B`, and supplied price `Pₜ`, the component executes
`Iₜ = B × (Pₜ / P_b)`. At the base observation, the result is exactly `B`.

## Worked example

For prices `(80, 100, 60, 120)`, base index `1`, and base value `100`, the index is
`(80, 100, 60, 120)`. Using base value `1` instead produces `(0.8, 1, 0.6, 1.2)`; the path is
identical and only the display scale changes.

## Visualization

The component emits a line chart containing every index value. Timestamps become the horizontal
labels when supplied; otherwise source-order indexes are shown and chronology is marked
unverified. The base convention, assumptions, and state-dependent warnings remain attached to the
chart.

## Common mistakes

- Index levels are not periodic returns. A move from 100 to 80 is a 20% decline, not a “return of
  80%.”
- Separate instruments need separately calculated indexes; the component does not align calendars
  or combine currencies.
- “Adjusted” is provider-defined. The component does not determine whether dividends were
  reinvested.

## Limitations

The component does not fetch, clean, sort, resample, align, aggregate, or currency-convert data.
It is not a total-return or portfolio-index engine, and it does not calculate drawdown or
volatility. Calendar-aware gaps remain unassessed. Unrepresentable finite results are blocked.
