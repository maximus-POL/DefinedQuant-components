# Component ideas and implemented extensions

This is a lightweight roadmap for Defined Quant components. It is not a source of financial
conventions: canonical inputs, outputs, units, behavior, guidance, and semantic ports live in each
component's five files under `categories/<category>/<component>/`.

## Implemented in the performance-visualization extension

### Rebased price index

**ID:** `dq.market_data.rebased_price_index`

Shows the direction and complete cycle of one ordered positive price series. The caller explicitly
supplies a zero-based base observation and positive base value; the component never silently
assumes 100. It emits the complete unitless index path, indexed lineage, interpretation state, and
a line chart.

### Drawdown / underwater series

**ID:** `dq.performance.drawdown`

Measures every price below its running peak and emits the full underwater path, maximum drawdown,
selected peak/trough/recovery indexes and timestamps, indexed lineage, and a line chart. Equal
highs update the peak to the latest high, tied deepest troughs select the earliest trough, and
recovery means the first later observation at or above the selected peak.

This component introduced the `performance` category for deterministic path-dependent measures.

### Rolling historical volatility

**ID:** `dq.volatility.rolling_historical_volatility`

Calculates sample standard deviation over every complete window of log periodic returns, then
annualizes each observation with an explicit periods-per-year factor. Window length, return kind,
and annualization factor have no defaults. The component emits periodic and annualized series,
window-end alignment, indexed lineage, and a line chart.

### Monthly-return matrix

**ID:** `dq.market_data.monthly_return_matrix`

Calculates simple returns from consecutive completed month-end prices and emits a calendar
heatmap. The caller must explicitly assert `completed_month_end`; missing months are blocking, so a
multi-month change is never mislabeled as a one-month return. The chart uses a closed, symmetric
red-neutral-green scale around zero.

This implementation added `heatmap` to the trusted closed visualization vocabulary. Heatmaps
accept exactly one finite series with unique, strictly increasing `YYYY-MM` categories; styling
remains internal to the renderer and no arbitrary SVG, HTML, CSS, or JavaScript is accepted.

## Adjacent future ideas

- Multi-instrument comparison with explicit date alignment, currency, missing-observation, and
  weighting policies.
- Calendar aggregation from daily prices with explicit timezone, exchange calendar, and partial
  period rules.
- Drawdown-duration components with explicit observation-count, calendar-day, or trading-session
  units.
- Rolling estimators for downside deviation or robust scale, each with a separate convention and
  semantic port.
- Statistical seasonality tests distinct from descriptive monthly-return heatmaps.

Future components should continue to use canonical authoring tools, explicit conventions,
synthetic evidence, closed semantic ports, and the blocking-versus-warning rules in `AGENTS.md`.
