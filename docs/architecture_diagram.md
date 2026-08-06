# MediPay GraphRAG Architecture

## System Overview

```mermaid
graph TB
    User([User]) --> Web[Next.js app / web]
    Web -->|REST| API[FastAPI / src/api]
    API --> Agent[LangGraph GraphRAG]
    Agent --> DB[(Supabase PostgreSQL + pgvector)]
    Agent --> Model[Unconfigured local model adapter]
    Agent --> Answer[Grounded answer + citations]
```

## Agent Flow

```mermaid
graph LR
    START((START)) --> Intake[Intake]
    Intake --> Entities[Extract entities]
    Entities --> Vector[Vector retrieval]
    Vector --> Graph[Graph expansion]
    Graph --> Context[Assemble evidence]
    Context --> Generate[Generate]
    Generate --> Guardrail[Guardrail]
    Guardrail --> END((END))
```

## Components

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Frontend | Next.js in `web/` | Future user interface |
| Backend | FastAPI in `src/` | API and application services |
| Agent | LangGraph | GraphRAG orchestration |
| Database | Supabase PostgreSQL | Shared relational persistence |
| Vector | PostgreSQL `pgvector` | Semantic chunk retrieval |
| Graph | PostgreSQL entity/relation tables | Graph neighborhood retrieval |
| Models | Provider-neutral adapters | Local LLM/embedding decision pending |

Schema SQL lives in `supabase/migrations/0001_initial_graphrag.sql`. Docker runs backend only and connects to Supabase.
