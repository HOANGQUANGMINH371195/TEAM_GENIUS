# AI contract — Ansible

This directory bootstraps only the AWS host. The playbook is idempotent,
requires immutable image digests, keeps secrets `no_log`/0600 and never belongs
to the FastAPI request path. Do not add application logic here.
