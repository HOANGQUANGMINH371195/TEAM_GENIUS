# Monitoring contract

Prometheus scrapes only the internal API network with a bearer token file;
Grafana reads Prometheus and retains seven days/2 GB by default. Do not add
user IDs, prompts, document IDs or response text to labels or dashboards.
