# Log Return

> Experimental Technical Preview. This component is draft, has author-supplied evidence, and has
> no domain review. Canonical inputs, outputs, units, defaults, and semantic ports live in
> `component.py`.

## Intuition

A log return measures the natural-log change between each pair of successive positive prices.
Log returns add across adjacent periods, which makes them useful when an explicitly log-return
downstream calculation consumes the series. This component preserves caller order and never
sorts, reverses, fills, fetches, or adjusts observations.

## Formula

For ordered strictly positive prices \(P_0, P_1, \ldots, P_n\), the canonical executed formula is
`rₜ = log1p((Pₜ − Pₜ₋₁) / Pₜ₋₁) if Pₜ ≥ Pₜ₋₁ / 2 and Pₜ₋₁ ≥ Pₜ / 2; otherwise q = Pₜ / Pₜ₋₁ and rₜ = log(q) if 2⁻¹⁰²² ≤ q < ∞, else log(Pₜ) − log(Pₜ₋₁)` for
\(t=1,\ldots,n\). Within the factor-of-two neighborhood, binary64 subtraction is exact and
`log1p` preserves changes close to zero. Outside that neighborhood, taking the log of a finite,
normal price ratio avoids cancellation between two large logarithms. The final log-difference
branch is used when the binary64 ratio is subnormal, underflows to zero, or overflows to infinity,
because a subnormal quotient can have too little relative precision for its logarithm.

The output uses decimal log units. Its declared return convention is `log`, not `simple`.

## Worked example

For prices `[100.0, 105.0, 102.9]`, the output is approximately
`[0.04879016416943201, -0.0202027073175194]`. Their sum is approximately
`0.0285874568519126`, equal to `log(102.9) - log(100)`. Exponentiating that sum gives the terminal
price ratio `1.029`.

## Datapoint lineage

Every `returns[i]` has one closed `Derivation`. It names `returns[i]`, references `prices[i]` and
`prices[i + 1]` in order, records whether the `log1p`, finite-ratio log, or log-difference branch
actually ran, and repeats the exact result value. `InputRef.citation_id` is currently `null`; it is
the stable join point for exact upstream source-cell citations. The expression is inspectable
metadata and is never evaluated as code.

## Semantic ports

The price-series and price-kind inputs, and the return-series and return-kind outputs, carry closed
semantic-port metadata in their generated Pydantic JSON Schemas. The output port explicitly marks
the series as ordered decimal `log_periodic_return` values. A composition layer can therefore
distinguish it from a simple-return producer without importing or guessing from prose.

## Visualization

Every result contains one renderer-neutral line-chart specification whose values are exactly the
full numerical return tuple. Dense charts retain every return in the line while omitting point
markers that would overlap at the selected width.

With timestamps, points use interval-end timestamps. Without timestamps, labels use observation
end indices and a warning states that chronology was not verified.

## Common mistakes

- Treating a log return as a simple percentage change.
- Mixing simple and log returns downstream.
- Omitting whether prices are adjusted or unadjusted.
- Calling an unadjusted price change a total return.
- Sorting observations silently instead of refusing reversed chronology.
- Treating a declared frequency as an exchange calendar or gap policy.

## Appropriate uses

Use this component when the return convention is explicitly log, prices are strictly positive and
ordered, and adjusted versus unadjusted semantics are known. It is suitable for downstream
composition only when the consumer's semantic input port accepts log periodic returns.

## Inappropriate uses

Do not use it to source prices, build dividend-reinvested returns, convert currencies, repair
observations, calculate simple returns, annualize performance, or estimate risk.

## Limitations

The component does not establish that the supplied series is complete, point-in-time correct,
survivorship-bias free, or fit for investment decisions. It does not audit a provider's adjustment
method. Frequency is never inferred, and calendar-aware gaps are not assessed. Those permanent
limitations appear in `disclosures`; warnings are reserved for input-dependent findings such as
unverified ordering, unadjusted-price interpretation, or an omitted over-limit chart.
