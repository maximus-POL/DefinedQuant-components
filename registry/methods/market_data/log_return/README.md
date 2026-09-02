# Log Return

> Experimental Technical Preview. Lifecycle: draft. No independent domain review or independent
> reproduction is claimed.

Log Return is the user-facing method for natural-log changes between adjacent positive prices.
Its canonical MethodSpec is `method.yaml`; this README is explanatory and does not redefine the
method contract or the `returns.log` capability.

For each adjacent pair the financial quantity is `ln(Pₜ / Pₜ₋₁)`. Log returns add across adjacent
periods, but they are not simple percentage changes. Caller order is preserved and observations
are never fetched, sorted, filled, adjusted, or currency-converted.

The caller must state whether prices are adjusted or unadjusted. Optional timestamps establish
chronology and align each result to the interval end. A supplied frequency is retained as
disclosure only; it is not a calendar or gap policy.

The method does not construct total returns, validate provider adjustment policy, annualize
performance, estimate risk, authenticate data, or establish suitability for investment decisions.
Implementations of `returns.log` must satisfy the same backend-independent conformance cases,
including close-price and extreme-ratio boundaries.
