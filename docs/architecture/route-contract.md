# Route contract

`src/domain/route_plan.py` creates a typed, bounded `RoutePlan` from query
shape. It is a routing budget, not an answer or a source of legal facts.

Routes are `policy`, `exact`, `table`, `topical`, `temporal`, `relational`,
`global`, and `deep`. Each plan records risk, required facts, permitted
providers, retrieval/generation budgets, candidate/context ceilings, and the
verifier policy. Plans are emitted in server-side trace metadata only.

Rules:

- policy and exact routes do not invoke Qdrant or Neo4j;
- table routes must pass verified values to the Decimal calculator;
- graph is allowed only for temporal/relational retrieval and must hydrate
  canonical PostgreSQL passages before citation;
- global retrieval is opt-in (`FEATURE_GLOBAL_SEARCH_ENABLED`) and may use a
  release-matched community index only to seed document IDs; its summaries
  are never evidence and every selected passage is hydrated from PostgreSQL;
- high-risk routes require authority, conditions, exceptions, and effective
  interval checks;
- a provider timeout degrades to the primary route or a bounded abstention.
- when the retrieval deadline expires, optional embedding/Qdrant work is
  cancelled and release-scoped lexical evidence remains the safe floor;
  table routes retain their structured-fact path.
- deep/global requests that may exceed the interactive budget can use the
  owner-isolated `POST /api/v1/research/jobs` endpoint; clients poll or cancel
  with the same `conversation_id`. The Redis worker persists only the public
  answer/citation envelope and never makes a summary or graph path a citation.

Changing a route requires a paired latency and legal-accuracy evaluation.
