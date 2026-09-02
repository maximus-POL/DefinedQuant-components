# Historical Volatility

> Experimental Technical Preview. Lifecycle: draft. No independent domain review or independent
> reproduction is claimed.

Historical Volatility is a descriptive method for sample standard deviation of explicit log
returns and square-root annualization. The MethodSpec in `method.yaml` is canonical.

The recipe separates two reusable concerns. `statistics.sample_standard_deviation` estimates the
periodic dispersion with an `n − 1` denominator. `statistics.square_root_annualize` applies the
caller-supplied periods-per-year factor. Neither capability chooses a library or backend.

The caller must explicitly confirm log returns and provide the annualization factor. The method
does not infer frequency, convert returns, inspect timestamps, or decide whether square-root scaling
is appropriate for the data-generating process.

The result is backward-looking and descriptive. It is not implied volatility, a forecast, a
confidence interval, a robust estimator, or investment advice.
