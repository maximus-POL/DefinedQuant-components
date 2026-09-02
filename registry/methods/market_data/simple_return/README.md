# Simple Return

> Experimental Technical Preview. Lifecycle: draft. No independent domain review or independent
> reproduction is claimed.

Simple Return is the user-facing financial method for calculating adjacent-period price changes
from an explicitly ordered price series. The canonical MethodSpec is `method.yaml`; this README is
explanatory and does not redefine its schemas, conventions, constraints, or recipe.

For consecutive prices, each decimal return is the change in price divided by the starting price
for that interval. A result of `0.05` means 5%. The method preserves caller order and never fetches,
sorts, fills, adjusts, or currency-converts observations.

The caller must state whether prices are adjusted or unadjusted. That choice does not alter the
arithmetic, but it materially changes interpretation. Unadjusted price changes exclude reinvested
distributions and must not be described as total returns.

When timezone-aware timestamps are supplied, each return is aligned to its interval-end timestamp
and chronology is verified. Without timestamps, order is accepted as supplied and marked
unverified. A frequency label is disclosure only and never substitutes for a market calendar.

The method is inappropriate for log returns, total-return construction, annualization, volatility,
corporate-action adjustment, gap repair, source authentication, or data acquisition. Its result
describes the supplied observations; it does not establish that they are complete, point-in-time
correct, survivorship-bias free, or suitable for an investment decision.

The initial registered realization is the local DQ-native implementation of the
`returns.simple` capability. The financial method does not embed or require that backend.
