# Historical Volatility

> Experimental Technical Preview. This component is draft, has author-supplied evidence, and has
> no domain review. Canonical inputs, outputs, units, defaults, and semantic ports live in
> `component.py`.

## Intuition

Historical volatility describes how dispersed a supplied sample of periodic returns is. This
component first estimates the sample standard deviation of explicit log returns, then scales that
periodic estimate by the square root of a caller-supplied annualization factor. It does not infer
the factor from words such as “daily,” inspect timestamps, or convert simple returns to log
returns.

## Formula

For \(n \ge 2\) log periodic returns, the canonical executed algorithm is
`a = r₀; cᵢ = rᵢ − a; dᵢ = cᵢ if every cᵢ is finite, otherwise dᵢ = rᵢ; s = maxᵢ |dᵢ|; if s = 0, σ̂ = 0; otherwise μ = fsum(dᵢ / s) / n, h₀ = 0, hᵢ₊₁ = hypot(hᵢ, dᵢ / s − μ), and σ̂ = s × (hₙ / sqrt(n − 1)); σ̂annual = σ̂ × sqrt(A)`.
Here \(A > 0\) is the explicit number of observation periods per year. Centering on the first
return preserves small dispersion around a large common offset. If a centered subtraction
overflows, the raw values enter the same scaled `fsum` and ordered `hypot` fold instead. The
zero-scale branch returns exact zero. These branches expose the binary64 evaluation that actually
runs while retaining the mathematical meaning of an \(n - 1\) sample standard deviation.

Both outputs use decimal volatility units. For example, `0.20` means volatility of `20%`, not
`0.20%`. The returned `degrees_of_freedom_adjustment` value is `1`, which produces the
denominator \(n - 1\); it is not a claim that the sample has only one residual degree of freedom.

## Worked example

For log returns `[-0.01, 0.01]`, the anchor is `-0.01`, the finite centered values are
`[0.0, 0.02]`, and the scale is `0.02`. Their scaled values are `[0.0, 1.0]`, so `μ = 0.5` and the
ordered fold gives `h₂ = hypot(hypot(0, -0.5), 0.5) ≈ 0.7071067812`. The executed periodic result
is therefore `0.02 × (0.7071067812 / sqrt(1)) ≈ 0.0141421356`. With the explicit factor `A = 4`,
the annualized result is `0.0141421356 × sqrt(4) ≈ 0.0282842712`.

## Datapoint lineage

The output contains exactly two scalar `Derivation` records. The first targets
`periodic_volatility[0]` and references every supplied return in order. The second targets
`annualized_volatility[0]`, references those same returns plus `annualization_factor[0]`, and
records the square-root scaling expression. Both expressions identify whether the centered,
raw-fallback, or exact-zero branch ran. Scalar index zero is the canonical lineage address; the
expressions are inspectable metadata and are never evaluated as code.

## Semantic ports

The return-series and return-kind inputs require the log-periodic convention, and the explicit
annualization factor has its own scalar port. The periodic and annualized volatility outputs use
distinct concepts and frequency semantics. The annualized output uses the composite
`sample_standard_deviation_n_minus_1_square_root_annualization` convention, so sharing the same
scaling rule cannot make a population estimator falsely compatible with this sample estimator. A
Log Return producer matches both required return ports; a Simple Return producer differs on
convention. Runtime sample-size and factor constraints still apply after port compatibility
succeeds.

## Warnings and disclosures

Samples with fewer than 30 returns remain calculable but receive a `small_sample` warning. At 30
observations the warning clears. The explicit-factor and no-calendar statements are permanent
interpretation context, so they appear in `disclosures`, not in state-dependent `warnings`.

The component emits no visualization. A caller may present the two scalar values, but any chart
over the source series belongs to the upstream return component or a separate visualization step.

## Common mistakes

- Passing simple returns while labelling them as log returns.
- Supplying an unexplained annualization default such as `252` or inferring one from a frequency
  word.
- Treating the \(n - 1\) sample estimate as a population standard deviation.
- Reading decimal volatility `0.20` as `0.20%` instead of `20%`.
- Assuming square-root-of-time scaling is a forecast or is reliable under serial dependence and
  changing variance.
- Calling the output “realized volatility” when a different estimator, sampling scheme, or
  intraday construction is intended.

## Appropriate uses

Use this component when the caller supplies a finite sample of comparable log periodic returns,
states the log convention, chooses the periods-per-year factor, and wants a deterministic sample
standard deviation plus its square-root annualization. Its semantic input ports can accept a
compatible component-bound log-return series and convention without turning catalog dependency
metadata into an execution pipeline.

## Inappropriate uses

Do not use it to convert returns, source or clean data, infer observation frequency, repair gaps,
estimate implied volatility, forecast future risk, fit a GARCH-family model, calculate downside
deviation, or produce confidence intervals. Those tasks require distinct contracts and evidence.

## Limitations

The result is descriptive of the supplied sample. It does not establish data quality,
independence, stationarity, representativeness, or investment suitability. The factor is echoed
but not audited against timestamps or a calendar. Square-root annualization is an explicit caller
choice. Finite inputs are rejected if the periodic or annualized result cannot be represented
without overflow, or if meaningful non-zero dispersion would round to exact zero. Constant
returns legitimately produce zero volatility, including under a very small positive factor.
