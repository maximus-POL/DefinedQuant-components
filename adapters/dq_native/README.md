# Defined Quant DQ-native adapter

`defined-quant-adapter-dq-native` is the independently installable local adapter distribution for
DQ-native capability implementations. It has no provider SDK, network, credential, or core-runtime
dependency.

The distribution advertises one safe `defined_quant.adapters` entry-point key for every registered
DQ-native capability realization. Reading installed entry-point metadata does not import this
package. Loading or invoking any entry point is permitted only after Defined Quant has compiled an
exact implementation, verified the pinned artifact, and admitted it under active host policy.
Installation alone never makes an adapter trusted or eligible.

Capability kernels contain pure deterministic calculations. Adapter modules contain canonical
input/output mapping and safe failure translation only. They do not select a backend, perform
fallback, or redefine a financial method. The distribution currently realizes simple return, log
return, monthly calendar alignment, explicit-base price rebasing, drawdown, scalar and rolling
sample standard deviation, and scalar and series square-root annualization.
