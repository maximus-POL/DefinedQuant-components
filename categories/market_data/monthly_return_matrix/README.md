# Monthly Return Matrix

> Experimental Technical Preview. This component is draft, has author-supplied evidence, and has
> no domain review. Canonical inputs, outputs, units, defaults, and semantic ports live in
> `component.py`.

## Intuition

A monthly-return heatmap puts years on rows and calendar months on columns. Green cells show
positive simple price returns, red cells show negative returns, and color intensity shows
magnitude on a symmetric observed scale. The grid makes broad return regimes visible without
claiming that a recurring pattern is statistically significant.

## Formula

For consecutive completed month-end prices, the component executes
`r_m = (P_m − P_{m−1}) / P_{m−1}`. Each return belongs to the ending month. The first supplied
price establishes the starting level and therefore has no return cell of its own.

## Worked example

For months `(2022-11, 2022-12, 2023-01, 2023-02)` and prices `(100, 80, 88, 79.2)`, returns are
`(−0.20, 0.10, −0.10)` aligned to `(2022-12, 2023-01, 2023-02)`.

## Visualization

The component emits a deterministic calendar heatmap. Its closed chart specification accepts one
finite return series with unique, strictly increasing `YYYY-MM` categories. Missing grid cells
before or after the supplied range remain neutral gray; missing months inside the range are
blocked before calculation.

## Common mistakes

- The component does not aggregate daily data. The caller must supply completed month-end prices.
- A partial current month is not comparable with completed months and must not be asserted as a
  completed month end.
- Similar colors in the same calendar month do not by themselves establish seasonality.

## Limitations

The component does not fetch, clean, sort, align, resample, or detect month-end observations. It
does not repair missing months, reconstruct total returns, convert currencies, test seasonality,
or forecast regimes. Adjustment and completed-month semantics are explicit caller assertions.
