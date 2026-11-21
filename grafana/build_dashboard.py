#!/usr/bin/env python3
"""Writes grafana/llamaswap-dashboard.json. The datasource is a dashboard variable (${datasource})."""
import json
from pathlib import Path

DS = {"type": "prometheus", "uid": "${datasource}"}


def panel(pid, title, exprs, x, y, w=12, h=8, unit="short", kind="timeseries", desc=""):
    p = {"id": pid, "type": kind, "title": title, "description": desc, "datasource": DS,
         "gridPos": {"x": x, "y": y, "w": w, "h": h},
         "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
         "targets": [{"refId": chr(65 + i), "datasource": DS, "expr": e, "legendFormat": l}
                     for i, (e, l) in enumerate(exprs)]}
    if kind == "stat":
        p["options"] = {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "value"}
    return p


def q(metric, quantile, by=""):
    group = f"le{', ' + by if by else ''}"
    return f"histogram_quantile({quantile}, sum by ({group}) (rate({metric}_bucket[$__rate_interval])))"


panels = [
    panel(1, "Requests / min", [("sum(rate(llamaswap_request_status_total[$__rate_interval])) * 60", "requests")],
          0, 0, w=6, h=4, kind="stat"),
    panel(2, "Median decode speed", [(q("llamaswap_request_decode_tps", 0.5), "tok/s")], 6, 0, w=6, h=4,
          kind="stat", desc="Requests with at least 16 output tokens"),
    panel(3, "Draft acceptance", [("sum(rate(llamaswap_request_draft_accepted_tokens_total[$__range])) / "
                                   "sum(rate(llamaswap_request_draft_tokens_total[$__range]))", "accepted")],
          12, 0, w=6, h=4, unit="percentunit", kind="stat", desc="Speculative decoding (MTP / draft model)"),
    panel(4, "Exporter up", [("min(llamaswap_exporter_up)", "up")], 18, 0, w=6, h=4, kind="stat"),
    panel(5, "Decode speed by context depth (median)", [(q("llamaswap_request_decode_tps", 0.5, "depth"), "{{depth}}")],
          0, 4, unit="none", desc="Generation tokens per second, grouped by context size"),
    panel(6, "Prefill speed (median, p10)", [(q("llamaswap_request_prefill_tps", 0.5), "p50"),
                                            (q("llamaswap_request_prefill_tps", 0.1), "p10")], 12, 4, unit="none"),
    panel(7, "Time to first token, approx. (p50 / p95)", [(q("llamaswap_request_prefill_seconds", 0.5), "p50"),
                                                          (q("llamaswap_request_prefill_seconds", 0.95), "p95")],
          0, 12, unit="s", desc="New prompt tokens divided by prompt speed"),
    panel(8, "Request duration (p50 / p95)", [(q("llamaswap_request_duration_seconds", 0.5), "p50"),
                                              (q("llamaswap_request_duration_seconds", 0.95), "p95")], 12, 12, unit="s"),
    panel(9, "Context per request (p50 / p95)", [(q("llamaswap_request_context_tokens", 0.5), "p50"),
                                                 (q("llamaswap_request_context_tokens", 0.95), "p95")], 0, 20),
    panel(10, "Token throughput", [("rate(llamaswap_total_output_tokens[$__rate_interval])", "generated"),
                                   ("rate(llamaswap_total_new_prompt_tokens[$__rate_interval])", "new prompt"),
                                   ("rate(llamaswap_total_cache_tokens[$__rate_interval])", "from cache")],
          12, 20, unit="none", desc="tokens/s, from llama-swap's running totals"),
    panel(11, "Requests by HTTP status", [("sum by (code) (rate(llamaswap_request_status_total[$__rate_interval])) * 60",
                                           "{{code}}")], 0, 28, unit="none"),
    panel(12, "Requests missed between polls", [("increase(llamaswap_exporter_missed_requests_total[$__range])",
                                                 "missed")], 12, 28, w=6, kind="stat",
          desc="Non-zero: raise ACTIVITY_LIMIT or lower POLL_INTERVAL"),
    panel(13, "Last model", [("llamaswap_active_model_info", "{{model}}")], 18, 28, w=6, kind="stat"),
]
panels[-1]["options"]["textMode"] = "name"

dashboard = {
    "title": "llama-swap requests", "uid": "llamaswap-requests", "schemaVersion": 39, "version": 2,
    "tags": ["llama-swap", "llm"], "time": {"from": "now-24h", "to": "now"}, "refresh": "1m",
    "templating": {"list": [{"name": "datasource", "label": "Prometheus", "type": "datasource",
                             "query": "prometheus", "current": {}, "hide": 0}]},
    "panels": panels,
}
out = Path(__file__).with_name("llamaswap-dashboard.json")
out.write_text(json.dumps(dashboard, indent=2) + "\n")
print(out)
