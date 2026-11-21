#!/usr/bin/env python3
"""llamaswap-exporter: Prometheus metrics for the requests llama-swap serves.

llama-swap's own /metrics covers the machine (CPU, memory, GPU). This exporter reads its activity API
(/api/metrics/stats and /api/metrics/activity) and turns every finished request into histograms: decode and
prefill speed, time to first token (approx.), duration, context size, speculative-draft acceptance and HTTP
status, plus llama-swap's running token totals and the most recently used model.

Settings (environment):
  LLAMASWAP_URL          llama-swap base URL                      (default http://127.0.0.1:8080)
  LLAMASWAP_API_KEY      sent as "Authorization: Bearer ..." when llama-swap has apiKeys set (default none)
  EXPORTER_LISTEN        listen address                           (default 0.0.0.0)
  EXPORTER_PORT          listen port                              (default 9105)
  POLL_INTERVAL          seconds between polls                    (default 15)
  ACTIVITY_LIMIT         activity records read per poll, 1-999    (default 200)
  INPUT_INCLUDES_CACHE   1 = llama-swap v269+ (input_tokens is the whole prompt, cache included);
                         0 = v268 and older (input_tokens counted new prompt tokens only)   (default 1)
  DEPTH_BUCKETS          context-size edges for the decode-speed "depth" label, in tokens
                         (default 8192,32768,65536,131072)
"""
import json
import os
import sys
import time
import urllib.request

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, Info, start_http_server


def _env_int(name, default):
    return int(os.environ.get(name, default))


def _depths(raw):
    edges = sorted(int(x) for x in raw.replace(" ", "").split(",") if x)
    labels, low = [], 0
    for edge in edges:
        labels.append((edge, f"{_short(low)}-{_short(edge)}"))
        low = edge
    return labels, f"{_short(low)}+"


def _short(n):
    return f"{n // 1024}k" if n and n % 1024 == 0 else str(n)


class Exporter:
    def __init__(self, base_url, api_key=None, activity_limit=200, depth_buckets="8192,32768,65536,131072",
                 input_includes_cache=True, registry=None, fetch=None):
        self.input_includes_cache = input_includes_cache
        self.base = base_url.rstrip("/")
        self.api_key = api_key
        self.limit = max(1, min(999, int(activity_limit)))
        self.depths, self.top = _depths(depth_buckets)
        self.fetch = fetch or self._fetch
        self.seen_id = None
        r = self.registry = registry or CollectorRegistry()

        self.up = Gauge("llamaswap_exporter_up", "1 if the last poll of llama-swap's activity API succeeded",
                        registry=r)
        self.total_requests = Gauge("llamaswap_total_requests", "Total requests in llama-swap's activity store",
                                    registry=r)
        self.total_input = Gauge("llamaswap_total_input_tokens",
                                 "Total prompt tokens as llama-swap counts them (v269+: cache included)", registry=r)
        self.total_output = Gauge("llamaswap_total_output_tokens", "Total generated tokens", registry=r)
        self.total_cache = Gauge("llamaswap_total_cache_tokens", "Total prompt tokens served from cache", registry=r)

        self.last_duration_ms = Gauge("llamaswap_last_request_duration_ms", "Duration of the most recent request",
                                      registry=r)
        self.last_tps = Gauge("llamaswap_last_request_tokens_per_second", "Decode speed of the most recent request",
                              registry=r)
        self.last_pps = Gauge("llamaswap_last_request_prompt_per_second", "Prompt speed of the most recent request",
                              registry=r)
        self.active_model = Info("llamaswap_active_model", "Model of the most recent request", registry=r)

        self.decode_tps = Histogram("llamaswap_request_decode_tps",
                                    "Generation tokens/s per request (>= 16 output tokens), by context depth",
                                    ["depth"], buckets=(10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 120, 150, 200, 300),
                                    registry=r)
        self.prefill_tps = Histogram("llamaswap_request_prefill_tps",
                                     "Prompt tokens/s per request (>= 256 new prompt tokens)",
                                     buckets=(100, 250, 500, 750, 1000, 1500, 2000, 3000, 5000, 10000), registry=r)
        self.prefill_s = Histogram("llamaswap_request_prefill_seconds",
                                   "New prompt tokens / prompt speed: approx. time to first token",
                                   buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 40, 80, 160), registry=r)
        self.duration_s = Histogram("llamaswap_request_duration_seconds", "Whole request duration",
                                    buckets=(1, 2, 5, 10, 20, 40, 60, 120, 300, 600, 1200), registry=r)
        self.context_tok = Histogram("llamaswap_request_context_tokens", "Context per request: cached + new prompt",
                                     buckets=(1024, 4096, 8192, 16384, 32768, 65536, 98304, 131072, 196608, 262144),
                                     registry=r)
        self.draft_total = Counter("llamaswap_request_draft_tokens", "Speculative draft tokens proposed", registry=r)
        self.draft_acc = Counter("llamaswap_request_draft_accepted_tokens", "Speculative draft tokens accepted",
                                 registry=r)
        self.status = Counter("llamaswap_request_status", "Finished requests by HTTP status", ["code"], registry=r)
        self.missed = Counter("llamaswap_exporter_missed_requests",
                              "Requests that finished between polls but fell off the activity page", registry=r)
        # Start labelled series at 0 so rate() sees the first request.
        for _, label in self.depths:
            self.decode_tps.labels(label)
        self.decode_tps.labels(self.top)
        self.status.labels("200")

    def _fetch(self, path):
        request = urllib.request.Request(self.base + path)
        if self.api_key:
            request.add_header("Authorization", f"Bearer {self.api_key}")
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)

    def depth_label(self, n):
        return next((label for edge, label in self.depths if n < edge), self.top)

    def observe(self, records):
        """Record activity entries newer than the last seen id. The first poll only sets the baseline."""
        ids = [r.get("id", 0) for r in records]
        if self.seen_id is None:
            self.seen_id = max(ids, default=0)
            return
        new = sorted((r for r in records if r.get("id", 0) > self.seen_id), key=lambda x: x.get("id", 0))
        if new and len(records) >= self.limit and new[0].get("id", 0) > self.seen_id + 1:
            # The whole page is new and doesn't reach back to the last seen id: some requests were skipped.
            self.missed.inc(new[0]["id"] - self.seen_id - 1)
        for r in new:
            t = r.get("tokens") or {}
            given, cached, out = t.get("input_tokens", 0) or 0, max(t.get("cache_tokens", 0) or 0, 0), t.get("output_tokens", 0) or 0
            tps, pps = t.get("tokens_per_second", 0) or 0, t.get("prompt_per_second", 0) or 0
            # prompt = new (uncached) prompt tokens, ctx = whole prompt
            if self.input_includes_cache:
                ctx, prompt = given, max(given - cached, 0)
            else:
                ctx, prompt = given + cached, given
            self.status.labels(str(r.get("resp_status_code", 0))).inc()
            self.duration_s.observe(r.get("duration_ms", 0) / 1000)
            self.context_tok.observe(ctx)
            if out >= 16 and tps > 0:
                self.decode_tps.labels(self.depth_label(ctx)).observe(tps)
            if prompt >= 256 and pps > 0:
                self.prefill_tps.observe(pps)
            if prompt > 0 and pps > 0:
                self.prefill_s.observe(prompt / pps)
            self.draft_total.inc(max(t.get("draft_tokens", 0) or 0, 0))
            self.draft_acc.inc(max(t.get("draft_acc_tokens", 0) or 0, 0))
        self.seen_id = max(self.seen_id, max(ids, default=0))

    def poll(self):
        ok = True
        try:
            stats = self.fetch("/api/metrics/stats")
            self.total_requests.set(stats.get("total_requests", 0))
            self.total_input.set(stats.get("total_input_tokens", 0))
            self.total_output.set(stats.get("total_output_tokens", 0))
            self.total_cache.set(stats.get("total_cache_tokens", 0))
        except Exception as e:  # keep serving the last values
            ok = False
            print(f"stats poll failed: {e}", file=sys.stderr, flush=True)
        try:
            records = self.fetch(f"/api/metrics/activity?limit={self.limit}").get("data") or []
            self.observe(records)
            if records:
                latest = max(records, key=lambda x: x.get("id", 0))
                tokens = latest.get("tokens") or {}
                self.active_model.info({"model": str(latest.get("model", "unknown"))})
                self.last_duration_ms.set(latest.get("duration_ms", 0))
                self.last_tps.set(tokens.get("tokens_per_second", 0) or 0)
                self.last_pps.set(tokens.get("prompt_per_second", 0) or 0)
        except Exception as e:
            ok = False
            print(f"activity poll failed: {e}", file=sys.stderr, flush=True)
        self.up.set(1 if ok else 0)
        return ok


def main():
    exporter = Exporter(os.environ.get("LLAMASWAP_URL", "http://127.0.0.1:8080"),
                        api_key=os.environ.get("LLAMASWAP_API_KEY") or None,
                        activity_limit=_env_int("ACTIVITY_LIMIT", 200),
                        depth_buckets=os.environ.get("DEPTH_BUCKETS", "8192,32768,65536,131072"),
                        input_includes_cache=os.environ.get("INPUT_INCLUDES_CACHE", "1") != "0")
    listen, port = os.environ.get("EXPORTER_LISTEN", "0.0.0.0"), _env_int("EXPORTER_PORT", 9105)
    interval = float(os.environ.get("POLL_INTERVAL", "15"))
    start_http_server(port, addr=listen, registry=exporter.registry)
    print(f"llamaswap-exporter: {exporter.base} -> http://{listen}:{port}/metrics every {interval:g}s", flush=True)
    while True:
        exporter.poll()
        time.sleep(interval)


if __name__ == "__main__":
    main()
