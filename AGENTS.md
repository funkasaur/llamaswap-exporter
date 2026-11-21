# Agent instructions for llamaswap-exporter

## Scope

Python 3.10+ exporter that converts completed llama-swap activity records into per-request
Prometheus metrics.

## Repository layout

- `llamaswap_exporter.py` implements polling and metric aggregation.
- `requirements.txt` declares `prometheus-client`.
- `Dockerfile` and `examples/docker-compose.yaml` show container deployment.
- `examples/prometheus.yml` shows scrape configuration.
- `grafana/` contains the dashboard. `tests/` contains the offline test suite.

## Build, run and test

- Install dependencies with `pip install -r requirements.txt`.
- Run `LLAMASWAP_URL=http://127.0.0.1:8080 python3 llamaswap_exporter.py` with a local
  llama-swap instance.
- Run `python3 -B -m unittest discover -s tests -t . -v`.
- Build the image with `docker build -t llamaswap-exporter .` and use the README or example
  Compose file for deployment.
- Check the exporter's `/metrics` response when validating a running setup.

## Conventions

- This exporter uses llama-swap's activity API. Scrape llama-swap's own `/metrics`
  separately for machine telemetry.
- Preserve counter recovery when activity IDs restart and bounded per-model histogram
  labels.
- Keep decode, prefill, context and speculative-draft metric semantics aligned with the
  source records.
- Update the Grafana dashboard and sample scrape configuration when metric names or labels
  change.

## Validation notes

- Tests use activity fixtures and a fake API. Extend those fixtures when changing the
  meaning of a request metric.
- Check whether input tokens already include cache reads before changing prefill or context
  accounting.
- Keep the runtime image and Python invocation aligned with the declared dependency.
