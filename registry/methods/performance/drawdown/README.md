# Drawdown

> Experimental Technical Preview. Lifecycle: draft. No independent domain review or independent
> reproduction is claimed.

Drawdown measures a positive price path below its running highs. The canonical MethodSpec lives in
`method.yaml`; this README does not redefine the `performance.drawdown` capability.

Each value is `Pₜ / running_peakₜ − 1`, so zero identifies a running high and negative values show
depth below it. Equal highs replace the running-peak index. The earliest deepest trough is selected.
For a negative episode, recovery is the first later observation at or above the selected peak; a
zero-drawdown path is treated as immediately recovered at its selected peak and trough.

The caller must state whether prices are adjusted or unadjusted. Optional timestamps preserve
point identities, but the method does not infer calendars, gaps, or elapsed-time duration.

The result is descriptive. It does not estimate loss probability, value at risk, forecast risk,
authenticate a dataset, or repair prices and corporate actions.
