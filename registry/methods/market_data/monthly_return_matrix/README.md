# Monthly Return Matrix

> Experimental Technical Preview. Lifecycle: draft. No independent domain review or independent
> reproduction is claimed.

Monthly Return Matrix is the user-facing method for consecutive completed month-end price returns
and their calendar alignment. Its MethodSpec is canonical; this README is explanatory only.

The recipe deliberately composes two backend-neutral capabilities. `returns.simple` calculates
adjacent simple returns, and `returns.monthly_calendar_matrix` verifies and applies interval-end
calendar-month alignment. Neither capability chooses a backend or provider.

The caller must explicitly assert completed month-end observations and identify adjusted versus
unadjusted prices. Labels must use `YYYY-MM`, align one-to-one with prices, and cover consecutive
calendar months. The method neither aggregates daily data nor detects partial months.

The output can support a deterministic calendar heatmap, but recurring colors are descriptive.
They do not by themselves establish seasonality, significance, causality, or a forecast.
