# Rolling Historical Volatility

> Experimental Technical Preview. This component is draft, has author-supplied evidence, and has
> no domain review. Canonical inputs, outputs, units, defaults, and semantic ports live in
> `component.py`.

## Intuition

Rolling historical volatility applies the same sample-variability estimate to every complete
moving window. The line rises when recent log returns became more dispersed and falls when they
became quieter. This describes turbulence; it does not reveal whether prices moved up or down.

## Formula

For window length `w`, explicit periods-per-year factor `A`, and log returns `r`, the component
executes
`σ̂ₜ(w) = stdevₙ₋₁(rₜ₋w₊₁, …, rₜ); σ̂annual,ₜ(w) = σ̂ₜ(w) × sqrt(A)`.
Each estimate is aligned with the last return in its complete window.

## Worked example

For log returns `(0.01, −0.01, 0.03, 0.01)`, window length `3`, and annualization factor `12`, the
periodic sample volatilities are `0.02` and `0.02`. Both annualized values equal
`0.02 × sqrt(12)`, approximately `0.069282` or 6.93%.

## Visualization

The component emits the complete annualized rolling-volatility series as a percentage-formatted
line. Timestamp labels identify each window end when supplied. The window length, annualization
factor, assumptions, and warnings remain visible with the chart.

## Common mistakes

- Volatility has no sign. A high value means returns were dispersed, not that price went down.
- Annualization does not make a return annual; it rescales a standard-deviation estimate under an
  explicit square-root-of-time assumption.
- Overlapping windows share observations, so adjacent estimates are not independent.

## Limitations

The component accepts log returns only. It never infers window length, annualization factor,
frequency, market calendar, or missing-session policy. It is backward-looking, not a forecast,
confidence interval, implied-volatility measure, or changing-volatility model.
