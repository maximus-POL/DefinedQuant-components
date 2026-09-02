# Method and Capability ideas

This is a lightweight product roadmap, not a source of financial conventions. Canonical
user-facing contracts and Recipes live in `registry/methods/`; atomic typed interfaces and
universal conformance live in `registry/capabilities/`; exact executable realizations and their
evidence live in `registry/implementations/`, `registry/adapters/`, and `registry/evidence/`.

The public website may continue to call an aggregated Method projection a “Component page.” That
presentation term does not couple a Method to one Python implementation.

## Implemented Methods

### Rebased price index

**Method ID:** `dq.market_data.rebased_price_index`

Shows the direction and complete cycle of one ordered positive price series. The caller explicitly
supplies a zero-based base observation and positive base value; the Method never silently assumes
100. Its Recipe uses the backend-neutral price-rebasing Capability and publishes the complete
unitless index path, indexed lineage, interpretation state, and a line-chart specification.

### Drawdown / underwater series

**Method ID:** `dq.performance.drawdown`

Measures every price below its running peak and publishes the full underwater path, maximum
drawdown, selected peak/trough/recovery indexes and timestamps, indexed lineage, and a line-chart
specification. Equal highs update the peak to the latest high, tied deepest troughs select the
earliest trough, and recovery means the first later observation at or above the selected peak.

This Method introduced the `performance` taxonomy category for deterministic path-dependent
measures. The category organizes discovery; it does not own code.

### Rolling historical volatility

**Method ID:** `dq.volatility.rolling_historical_volatility`

Calculates sample standard deviation over every complete window of log periodic returns, then
annualizes each observation with an explicit periods-per-year factor. Window length, return kind,
and annualization factor have no implicit defaults. Its Recipe separates the rolling estimator
from square-root annualization so each Capability can have independently registered
Implementations.

### Monthly-return matrix

**Method ID:** `dq.market_data.monthly_return_matrix`

Calculates simple returns from consecutive completed month-end prices and publishes a calendar
heatmap specification. The caller must explicitly assert `completed_month_end`; missing months are
blocking, so a multi-month change is never mislabeled as a one-month return. The visualization uses
the trusted closed heatmap vocabulary rather than arbitrary SVG, HTML, CSS, or JavaScript.

## Candidate Methods and Capabilities

- A multi-instrument comparison Method with explicit date alignment, currency,
  missing-observation, and weighting policies. Reusable alignment and aggregation operations should
  be separate Capabilities.
- Calendar aggregation from daily observations with explicit timezone, exchange calendar, and
  partial-period rules. Data acquisition remains a separate Capability and provider choice remains
  a resolution constraint.
- Drawdown-duration Methods with explicit observation-count, calendar-day, or trading-session
  units, reusing the registered drawdown Capability where its contract fits.
- Rolling downside-deviation or robust-scale Methods, each backed by an explicitly defined atomic
  estimator Capability and implementation-independent conformance.
- Statistical seasonality Methods that remain distinct from descriptive monthly-return heatmaps.

Before implementation, each candidate needs an agreed Method contract, explicit conventions,
unsupported scope, backend-neutral Recipe, and the smallest reusable Capabilities. Do not add a
five-file component or a placeholder external Backend. Register an Implementation only when its
trusted Adapter and scoped evidence exist.

The old category component folders remain compatibility-only until **2026-12-31 or the first 0.2.0
release, whichever comes first**. They are not the authoring path for these ideas.
