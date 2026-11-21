---
name: deploy-llamaswap-exporter
description: Run the exporter and wire it into Prometheus and Grafana.
---

# Run the exporter and wire it into Prometheus and Grafana

1. Choose the documented Python invocation or build the image with `docker build -t llamaswap-exporter .`.
2. Use the Compose example or the README's run command. Configure `LLAMASWAP_URL` for the router.
3. Check the exporter's `/metrics` endpoint and add the scrape configuration from `examples/prometheus.yml`.
4. Import the dashboard in `grafana/`. Keep the router's own machine-metrics scrape alongside the exporter.
