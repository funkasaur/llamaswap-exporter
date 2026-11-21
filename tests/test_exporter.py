import io
import json
import unittest
from unittest import mock

from prometheus_client import generate_latest

import llamaswap_exporter as lx


def record(i, prompt=1000, cached=0, out=200, tps=50.0, pps=800.0, status=200, ms=4000, draft=0, acc=0, model="m"):
    return {"id": i, "model": model, "resp_status_code": status, "duration_ms": ms,
            "tokens": {"input_tokens": prompt, "cache_tokens": cached, "output_tokens": out,
                       "tokens_per_second": tps, "prompt_per_second": pps,
                       "draft_tokens": draft, "draft_acc_tokens": acc}}


class FakeAPI:
    def __init__(self):
        self.stats = {"total_requests": 3, "total_input_tokens": 100, "total_output_tokens": 50,
                      "total_cache_tokens": 10}
        self.records = []
        self.paths = []
        self.fail = False

    def __call__(self, path):
        self.paths.append(path)
        if self.fail:
            raise OSError("down")
        if path.startswith("/api/metrics/stats"):
            return self.stats
        return {"data": sorted(self.records, key=lambda r: -r["id"])}  # newest first, like llama-swap


class ExporterTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeAPI()
        self.x = lx.Exporter("http://swap:8080/", activity_limit=5, fetch=self.api)

    def value(self, name, labels=None):
        return self.x.registry.get_sample_value(name, labels or {})

    def test_first_poll_sets_baseline_only(self):
        self.api.records = [record(1), record(2)]
        self.assertTrue(self.x.poll())
        self.assertEqual(self.value("llamaswap_request_duration_seconds_count"), 0)
        self.assertEqual(self.value("llamaswap_total_requests"), 3)
        self.assertEqual(self.value("llamaswap_exporter_up"), 1)
        self.assertEqual(self.api.paths, ["/api/metrics/stats", "/api/metrics/activity?limit=5"])

    def test_new_records_fill_histograms_once(self):
        self.x.poll()
        self.api.records = [record(1, prompt=50000, cached=10000, draft=30, acc=20),
                            record(2, prompt=100, out=4, status=500, model="other")]
        self.x.poll()
        self.x.poll()  # same page again: nothing counted twice
        self.assertEqual(self.value("llamaswap_request_duration_seconds_count"), 2)
        self.assertEqual(self.value("llamaswap_request_decode_tps_count", {"depth": "32k-64k"}), 1)
        self.assertEqual(self.value("llamaswap_request_decode_tps_count", {"depth": "0-8k"}), 0)  # out < 16
        self.assertEqual(self.value("llamaswap_request_prefill_tps_count"), 1)                    # prompt < 256 skipped
        self.assertEqual(self.value("llamaswap_request_prefill_seconds_count"), 2)
        self.assertEqual(self.value("llamaswap_request_status_total", {"code": "500"}), 1)
        self.assertEqual(self.value("llamaswap_request_context_tokens_sum"), 50100)   # whole prompts, cache inside
        self.assertEqual(self.value("llamaswap_request_prefill_seconds_sum"), 40000 / 800 + 100 / 800)
        self.assertEqual(self.value("llamaswap_request_draft_tokens_total"), 30)
        self.assertEqual(self.value("llamaswap_request_draft_accepted_tokens_total"), 20)
        self.assertEqual(self.value("llamaswap_active_model_info", {"model": "other"}), 1)

    def test_missed_requests_counted_when_page_overflows(self):
        self.api.records = [record(1)]
        self.x.poll()
        self.api.records = [record(i) for i in range(10, 15)]  # full page (limit 5), ids 2-9 never seen
        self.x.poll()
        self.assertEqual(self.value("llamaswap_exporter_missed_requests_total"), 8)
        self.assertEqual(self.value("llamaswap_request_duration_seconds_count"), 5)

    def test_failure_sets_up_zero_and_keeps_values(self):
        self.x.poll()
        self.api.fail = True
        self.assertFalse(self.x.poll())
        self.assertEqual(self.value("llamaswap_exporter_up"), 0)
        self.assertEqual(self.value("llamaswap_total_requests"), 3)

    def test_old_llamaswap_input_counts_new_tokens_only(self):
        x = lx.Exporter("http://swap", input_includes_cache=False, fetch=self.api)
        x.poll()
        self.api.records = [record(1, prompt=40000, cached=10000)]
        x.poll()
        value = lambda name: x.registry.get_sample_value(name, {})
        self.assertEqual(value("llamaswap_request_context_tokens_sum"), 50000)
        self.assertEqual(value("llamaswap_request_prefill_seconds_sum"), 40000 / 800)

    def test_depth_labels_from_config(self):
        x = lx.Exporter("http://swap", depth_buckets="4096,16384", fetch=self.api)
        self.assertEqual([x.depth_label(n) for n in (0, 5000, 20000)], ["0-4k", "4k-16k", "16k+"])
        self.assertIn(b'depth="16k+"', generate_latest(x.registry))

    def test_limit_is_clamped_and_api_key_sent(self):
        self.assertEqual(lx.Exporter("http://swap", activity_limit=5000).limit, 999)
        x = lx.Exporter("http://swap:8080/", api_key="secret-key")
        body = io.BytesIO(json.dumps({"data": []}).encode())
        with mock.patch.object(lx.urllib.request, "urlopen", return_value=body) as urlopen:
            x._fetch("/api/metrics/activity?limit=1")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://swap:8080/api/metrics/activity?limit=1")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-key")


if __name__ == "__main__":
    unittest.main()
