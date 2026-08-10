# Simple Return

> Experimental Technical Preview. This component is draft, has author-supplied evidence, and has
> no domain review. Canonical inputs, outputs, units, and defaults live in `component.py`.

## Intuition

A simple return compares each supplied price with the immediately preceding supplied price. It
answers “how large was this price change relative to the starting price?” for every adjacent pair.
The component preserves caller order. It never sorts, reverses, fills, fetches, or adjusts data.

## Formula

For ordered prices \(P_0, P_1, \ldots, P_n\), the canonical executed formula is
`rₜ = (Pₜ − Pₜ₋₁) / Pₜ₋₁` for \(t=1,\ldots,n\). The difference is evaluated before division to
retain relative precision when adjacent binary64 prices are close.

The output uses decimal units: `0.05` means `5%`. Its declared return convention is `simple`, not
`log`.

## Worked example

For prices `[100.0, 105.0, 102.9]`:

- \((105 - 100) / 100 = 0.05\)
- \((102.9 - 105) / 105 = -0.02\)

The result is `[0.05, -0.02]`. Compounding the two outputs gives
\((1.05)(0.98)=1.029\), the same ratio as \(102.9/100\).

## Datapoint lineage

Every `returns[i]` has one closed `Derivation` record. It names `returns[i]` as the output,
references `prices[i]` and `prices[i + 1]` in order, records the indexed expression that ran, and
repeats the exact result value. `InputRef.citation_id` is currently `null`; it is the stable join
point where a later source adapter can attach exact source-cell citations without asking a
consumer to reconstruct the indexing rule from prose. The expression is inspectable metadata and
is never evaluated as code.

## Visualization

For results of up to 500 returns, the structured output contains one renderer-neutral
`VisualizationSpec`. Its plotted values are the same return tuple as the numerical result, with
decimal units and percentage display formatting. A trusted shared renderer can turn that closed
specification into SVG without asking the component to recalculate or passing through
caller-supplied markup.

For longer results, the full return tuple remains in `returns`, `visualizations` is empty, and a
`visualization_omitted` warning states the 500-point presentation limit. Presentation never
truncates or prevents the numerical result.

When timestamps are present, each point is labelled by the interval-end timestamp. Without
timestamps, labels are observation-end indices and the output visibly warns that chronology could
not be verified.

## Common mistakes

- Treating `0.05` as `0.05%` instead of `5%`.
- Choosing this component when the request says only “returns” and does not select simple versus
  log returns.
- Mixing simple and log returns downstream.
- Omitting whether prices are adjusted or unadjusted.
- Calling an unadjusted price change a total return.
- Sorting observations silently instead of refusing ambiguous or reversed chronology.
- Assuming that `daily` identifies exchange holidays, sessions, or a market calendar.

## Appropriate uses

Use this component for deterministic adjacent-period price returns when the ordered observations
and adjusted/unadjusted convention are explicit. It is also suitable as a trusted transform inside
an agent workflow because the result carries provenance, warnings, conventions, and, when the
result is within the presentation limit, its chart specification together. Each returned datapoint
also carries exact, machine-validated lineage to its two adjacent source prices.

## Inappropriate uses

Do not use it to source prices, infer adjustment policies, build dividend-reinvested returns,
perform currency conversion, repair missing observations, annualize performance, or estimate risk.
Those are separate data or analytical operations with their own assumptions.

## Limitations

The output describes the supplied series; it does not establish that the series is complete,
point-in-time correct, survivorship-bias free, or suitable for investment decisions. A declared
frequency is retained as disclosure only. Calendar-aware gap detection requires an explicit
calendar and gap policy that this draft contract does not yet define, so `gap_check` is always
`not_assessed`. A positive finite price pair whose return overflows or is indistinguishable from
total loss in binary64 is rejected.
