# Legacy component compatibility

The seven folders beneath `categories/` preserve the old bundled DQ-native component format for a
time-bounded compatibility window. They are not the canonical catalog, financial contract
hierarchy, or implementation registry, and no new component belongs here.

Canonical discovery and website taxonomy lives in `registry/taxonomy/categories.yaml`. Canonical
financial meaning and Recipes live in `registry/methods/`; atomic interfaces live in
`registry/capabilities/`; exact executable realizations live in `registry/implementations/` and
their trusted Adapter distributions.

The category README files remain human descriptions of financial topics. Category metadata may
organize search and website navigation, but it never selects a Backend or determines code
location.

The old folders and component catalog validation are removed on **2026-12-31 or the first 0.2.0
release, whichever comes first**. Until then they may be changed only to preserve behavior required
by an existing compatibility client. Their `subject_hash` identities do not become Method,
Capability, Adapter, Implementation, Plan, Step, or Run identities.
