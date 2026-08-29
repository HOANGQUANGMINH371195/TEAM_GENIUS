# AWS single-host bootstrap

`site.yml` configures Docker, the immutable production Compose stack, Nginx,
Prometheus/Grafana secrets and a restart-safe systemd unit. It does not create
AWS IAM keys, copy application credentials into Git, or run migrations during
every restart.

1. Copy `inventory.ini.example` and set the EC2 host reachable through SSH (or
   run Ansible from an SSM-connected bastion).
2. Put the multiline backend `.env` in an Ansible Vault vars file. Never pass it
   as a command-line value that can appear in shell history.
3. Run the host-only prerequisite phase once (`--tags host`), obtain the ACME
   certificate into `/opt/medipay/letsencrypt/live/<domain>/`, then run the full
   playbook. The full playbook refuses to start a broken TLS proxy when either
   certificate file is missing.

```bash
ansible-playbook -i inventory.ini ops/ansible/site.yml \
  --ask-become-pass --vault-password-file .vault-pass --tags host

ansible-playbook -i inventory.ini ops/ansible/site.yml \
  --ask-become-pass --vault-password-file .vault-pass \
  --extra-vars @vault-production.yml
```

The vars file must include `medipay_domain`, `medipay_api_image`,
`medipay_web_image` and `medipay_migrate_image` as `ghcr.io/...@sha256:...`, plus the secret values required
by the assertions in the playbook.
