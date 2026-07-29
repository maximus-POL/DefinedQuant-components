# Component categories

This is the public catalog. Each direct child folder is one financial topic, and each topic
contains inspectable components. GitHub shows those folders automatically, while
`authoring/export_catalog.py` produces the searchable machine index used by the website.

A component is always one folder directly inside a category. Open its `README.md` for the
financial explanation, `component.py` for the calculation, `contract.yaml` for the machine-readable
usage rules, `evidence.yaml` for the validation record, and `test_component.py` for executable
evidence. The required `discovery` block in every contract provides aliases, task intents, input
concepts, and output concepts for deterministic catalog-wide search without importing functions.
