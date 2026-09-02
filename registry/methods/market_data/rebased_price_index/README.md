# Rebased Price Index

> Experimental Technical Preview. Lifecycle: draft. No independent domain review or independent
> reproduction is claimed.

Rebased Price Index normalizes an ordered positive price path to a caller-selected observation and
display level. The MethodSpec in `method.yaml` is canonical; this README is explanatory only.

For base index `b` and base value `B`, each result is `B × Pₜ / P_b`. The output at the base
observation is exactly `B`. Changing `B` changes display scale, while positive rescaling of every
source price leaves the index unchanged.

The base index, base value, and adjusted-versus-unadjusted convention must all be explicit. The
method preserves order and optional timestamps, but performs no fetching, sorting, resampling,
currency conversion, corporate-action repair, or gap inference.

Index levels are not returns. This method is not a portfolio index, total-return construction,
drawdown calculation, volatility estimate, or source-authentication mechanism.
