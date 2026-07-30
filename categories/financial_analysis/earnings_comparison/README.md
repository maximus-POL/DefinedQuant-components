# Earnings Comparison

`dq.financial_analysis.earnings_comparison` compares one monetary earnings metric across a
complete, aligned panel of two to twelve companies.

## Intent

Use this component to turn caller-supplied revenue, operating-income, or net-income observations
into a normalized data table, company and period statistics, and a reusable chart suite. It is
designed for questions such as “How did Microsoft, Amazon, Meta, Alphabet, and other hyperscalers
compare over these periods?”

## When to use it

Use it when every company has one finite value for every ordered period, all values share the same
metric definition, reporting currency, and scale, and the fiscal-period alignment is explicit.

Do not use it to fetch or parse filings, mix currencies or accounting definitions, fill missing
data, compare per-share metrics, explain why performance changed, value a company, or forecast
future earnings.

## Calculations

For company \(i\), with ordered observations \(x_{i,1},\ldots,x_{i,n}\):

- arithmetic mean: \(\bar{x}_i = \frac{1}{n}\sum_t x_{i,t}\);
- sample standard deviation:
  \(s_i = \sqrt{\frac{1}{n-1}\sum_t(x_{i,t}-\bar{x}_i)^2}\);
- absolute endpoint change: \(\Delta_i = x_{i,n}-x_{i,1}\);
- percentage endpoint change: \(g_i = x_{i,n}/x_{i,1}-1\), only when \(x_{i,1}>0\);
- latest rank: one plus the count of strictly greater latest values;
- peer-set share: \(x_{i,n}/\sum_jx_{j,n}\), only when all latest values are non-negative
  and their total is positive.

The component also calculates total, mean, median, sample standard deviation, minimum, and maximum
across companies for every period. No time weighting, annualization, CAGR, FX conversion, or
inflation adjustment is applied.

## Inputs

The canonical types and constraints live in `Inputs` and `EarningsSeries` in `component.py`.
Important conventions are:

- `periods` are unique labels in intentional caller order; the component does not parse or sort
  them;
- `series` is a complete rectangular matrix with stable lower-snake-case company keys;
- `period_basis` distinguishes calendar-aligned observations from company-reported fiscal periods;
- `value_scale` describes the scale already applied to the numbers;
- `source_label` is preserved as caller-declared provenance, not independently verified evidence.

For upstream storage, use typed long-form records or a columnar format such as Parquet with columns
for period, company key, company label, metric, currency, scale, and value. Excel is useful as a
human review/export surface, but it should not be the calculation contract because cell types,
formulas, and units are easier to change silently.

## Output

`observations` is the normalized long-form data table. `company_statistics` is the main comparison
table, while `period_statistics` provides each period’s cross-sectional summary.

The visualization envelope contains:

1. a multi-company trend line;
2. a latest-period comparison bar chart;
3. an absolute-change bar chart;
4. a percentage-change bar chart for companies with a positive starting value;
5. a latest peer-set composition bar chart when its denominator is meaningful.

`dashboard` prescribes the primary view: chart order, primary-versus-secondary layout, table
columns, row ordering, numeric formats, captions, and notes. `view_bundle` declares responsive HTML
as the default chat format and SVG as the portable/print fallback. The shared trusted renderers
combine those specifications with the declared charts; agents must display the selected primary
artifact and must not create a replacement dashboard or restyle the result.

The HTML renderer emits a responsive, theme-aware fragment with deterministic company
highlighting and no network requests or component-supplied code. The individual chart
specifications remain available as supporting artifacts for drill-down. Their presence does not
authorize a host or agent to invent another primary composition.

## Worked example: synthetic hyperscalers

This sample is fictional, is denominated in USD millions, and must not be interpreted as reported
company data. It is repeated as an executable fixture in `test_component.py`.

```python
from defined_quant.financial_analysis.earnings_comparison.component import (
    earnings_comparison,
)

result = earnings_comparison(
    metric_name="Revenue",
    reporting_currency="USD",
    value_scale="millions",
    period_basis="calendar_aligned",
    periods=("2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4"),
    series=(
        {
            "key": "microsoft",
            "label": "Microsoft",
            "values": (61_800.0, 64_700.0, 65_600.0, 69_600.0),
        },
        {
            "key": "amazon",
            "label": "Amazon",
            "values": (143_300.0, 148_000.0, 158_900.0, 170_000.0),
        },
        {
            "key": "meta",
            "label": "Meta",
            "values": (36_500.0, 39_000.0, 40_500.0, 44_000.0),
        },
        {
            "key": "alphabet",
            "label": "Alphabet",
            "values": (80_500.0, 84_500.0, 88_000.0, 93_000.0),
        },
        {
            "key": "other_hyperscaler",
            "label": "Other hyperscaler",
            "values": (13_300.0, 13_800.0, 14_200.0, 14_600.0),
        },
    ),
    source_label="Synthetic illustrative fixture; not company filings",
)
```

For this sample, Amazon is the sole latest-value leader. `latest_leader_keys` is plural so tied
leaders are never resolved by arbitrary input order. The result includes 20 normalized observation
rows, five company-statistics rows, four period-statistics rows, five chart specifications, and one
prescribed dashboard with a deterministic multi-format view bundle.

## Assumptions and limitations

The component accepts the caller’s accounting definitions, fiscal mapping, source identification,
and currency conversion as supplied. `company_reported` periods generate a warning because labels
such as “Q1” may cover different dates. A zero or negative starting value makes percentage growth
economically misleading, so that value is omitted rather than forced.

The component does not calculate earnings surprises, analyst-consensus variance, margins, EPS,
CAGR, quality-of-earnings measures, segment attribution, valuation multiples, statistical
significance, causal explanations, or forecasts. Those are separate calculations with different
inputs and conventions.

## Evidence

`evidence.yaml` binds synthetic known answers, invariants, boundary behavior, and cross-checks to
named tests in `test_component.py`. The sample is deterministic and contains no vendor or scraped
market data.
