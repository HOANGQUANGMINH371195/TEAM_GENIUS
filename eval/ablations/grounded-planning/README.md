# Grounded planning ablation

Compare direct retrieval with evidence-gap planning (fan-out ≤3, depth ≤2).
Planning is eligible only when the evidence inventory identifies missing
material facts. Report completeness, cost, route latency and duplicate-branch
cancellation; reject any variant that adds latency without a supported gain.

`evaluate.py` is the deterministic report generator. It requires canonical
source hashes and direct/planned evidence IDs plus measured latency/cost for
each case; it leaves promotion eligibility to the independent review gate.
