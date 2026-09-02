# Rolling Historical Volatility

> Experimental Technical Preview. Lifecycle: draft. No independent domain review or independent
> reproduction is claimed.

Rolling Historical Volatility shows how backward-looking sample dispersion changes across every
complete fixed-length window of log returns. The MethodSpec in `method.yaml` is canonical.

The recipe composes `statistics.rolling_sample_standard_deviation` with
`statistics.square_root_annualize_series`. The first capability owns rolling-window estimation and
window-end alignment; the second owns explicit square-root scaling. Neither chooses a backend.

The caller must explicitly supply log returns, window length, and annualization factor. Optional
timestamps are preserved for window-end alignment, while frequency and calendar gaps are never
inferred. Adjacent windows overlap, so neighboring estimates are not independent.

This is a descriptive historical series, not a volatility forecast, implied volatility, automatic
window selection, confidence interval, or investment recommendation.
