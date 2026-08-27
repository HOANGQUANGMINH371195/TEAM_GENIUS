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
- high-risk routes require authority, conditions, exceptions, and effective
  interval checks;
- a provider timeout degrades to the primary route or a bounded abstention.
- when the retrieval deadline expires, optional embedding/Qdrant work is
  cancelled and release-scoped lexical evidence remains the safe floor;
  table routes retain their structured-fact path.

Changing a route requires a paired latency and legal-accuracy evaluation.
