create extension if not exists vector;

create table if not exists documents (
    id uuid primary key default gen_random_uuid(),
    title varchar(255) not null,
    source_uri varchar(1024) not null unique,
    content_hash varchar(64) not null,
    created_at timestamptz not null default now()
);

create table if not exists document_chunks (
    id uuid primary key default gen_random_uuid(),
    document_id uuid not null references documents(id) on delete cascade,
    content text not null,
    chunk_index integer not null,
    source_uri varchar(1024) not null,
    -- Add vector(<dimension>) and its index after local embedding model selection.
    embedding vector,
    created_at timestamptz not null default now()
);

create index if not exists ix_document_chunks_document_id
    on document_chunks(document_id);

create table if not exists entities (
    id uuid primary key default gen_random_uuid(),
    name varchar(255) not null,
    entity_type varchar(100) not null,
    description text not null default ''
);

create index if not exists ix_entities_name on entities(name);
create index if not exists ix_entities_type on entities(entity_type);

create table if not exists relations (
    id uuid primary key default gen_random_uuid(),
    source_entity_id uuid not null references entities(id) on delete cascade,
    target_entity_id uuid not null references entities(id) on delete cascade,
    relation_type varchar(100) not null,
    description text not null default ''
);

create index if not exists ix_relations_source_target
    on relations(source_entity_id, target_entity_id);

create table if not exists conversations (
    id uuid primary key default gen_random_uuid(),
    created_at timestamptz not null default now()
);

create table if not exists messages (
    id uuid primary key default gen_random_uuid(),
    conversation_id uuid not null references conversations(id) on delete cascade,
    role varchar(32) not null,
    content text not null,
    created_at timestamptz not null default now()
);
