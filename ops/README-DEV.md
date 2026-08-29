# Operations — developer guide

`ops/runbooks/` contains deploy, restore, observability and secret-rotation
procedures. `ops/monitoring/` contains Prometheus alert rules. Use `make
env-check`, `make deploy-contract` and the documented one-shot migration flow
before changing a deployment contract.

Vercel operator credentials belong in the local credential store; AWS runtime
secrets belong in Ansible Vault or the ignored host `.env`. Never put them in
`.env.example` or logs.
