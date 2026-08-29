# BHYT calculator contract

The calculator is a pure Decimal function. It never maps a question to a
percentage and never embeds a hard-coded legal rule. Retrieval/table
extraction supplies the already-verified rate, threshold, duration and
provenance.

`POST /calculator/bhyt` requires `covered_cost` and `base_rate_percent`.
Threshold logic additionally requires `continuous_years`; missing material
inputs return `422` instead of applying a guessed default. The response
contains insurer and patient amounts, threshold status, formula ID, and the
provenance references supplied by the verifier.

Money is rounded only at the final result using `Decimal` and half-up cents.
Every new legal formula must add deterministic golden fixtures before release.
