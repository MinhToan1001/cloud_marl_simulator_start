"""Light collector: kubectl JSON reads and app counters, with explicit fixtures."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import urlopen

from .parser import AppDeltaParser, kubernetes_records
from .schema import digest, utc_now, write_records


def http_json(url: str, timeout=5):
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("App URL must be HTTP(S), without embedded credentials")
    with urlopen(url, timeout=timeout) as response:
        data = response.read(4_000_001)
        if len(data) > 4_000_000:
            raise ValueError("App metrics response exceeds 4 MB")
        return json.loads(data)


class KubectlClient:
    def __init__(self, command: list[str], context: str, timeout=5):
        if not command or not all(isinstance(v, str) and v for v in command) or not context or "REPLACE" in context:
            raise ValueError("Explicit kubectl argv and a verified context are required")
        self.command, self.context, self.timeout = command, context, timeout

    def get(self, *arguments):
        # Caller can only issue the fixed GET operations below; no shell, exec or apply.
        result = subprocess.run([*self.command, "--context", self.context, "--request-timeout=5s", "get", *arguments],
                                capture_output=True, text=True, encoding="utf-8", timeout=self.timeout + 2, check=False)
        if result.returncode:
            raise RuntimeError(result.stderr.strip()[:500] or "kubectl GET failed")
        return json.loads(result.stdout)


def scrub_inventory(data: dict, kind: str):
    """Keep identities/resources/status; never persist pod env or annotations."""
    items = []
    for item in data.get("items", []):
        metadata = item.get("metadata", {})
        result = {"metadata": {key: metadata[key] for key in ("name", "namespace", "uid") if key in metadata},
                  "status": {key: item.get("status", {})[key] for key in ("phase", "conditions", "capacity", "allocatable") if key in item.get("status", {})}}
        if kind == "pods":
            result["spec"] = {"nodeName": item.get("spec", {}).get("nodeName", ""),
                              "containers": [{"name": c.get("name"), "resources": c.get("resources", {})}
                                             for c in item.get("spec", {}).get("containers", [])]}
        items.append(result)
    return {"items": items}


class LiveSource:
    def __init__(self, config: dict, http_get=http_json):
        self.config, self.http_get = config, http_get
        self.client = KubectlClient(config["kubectl_command"], config["context"])
        expected = config.get("expected_node_names", [])
        if not expected or any("REPLACE" in name for name in expected):
            raise ValueError("Configure verified expected_node_names before live collection")
        self.cursors = {}

    def snapshot(self):
        c = self.config
        now = utc_now()
        nodes = self.client.get("nodes", "-o", "json")
        actual = {node["metadata"]["name"] for node in nodes.get("items", [])}
        if actual != set(c["expected_node_names"]):
            raise ValueError("Context node identity differs from expected_node_names; refusing to mislabel cluster")
        snapshot = {"observed_at_utc": now, "nodes": scrub_inventory(nodes, "nodes"), "errors": [], "apps": []}
        operations = {
            "pods": ("pods", "-n", c["namespace"], "-l", c["label_selector"], "-o", "json"),
            "node_metrics": ("--raw", "/apis/metrics.k8s.io/v1beta1/nodes"),
            "pod_metrics": ("--raw", f"/apis/metrics.k8s.io/v1beta1/namespaces/{c['namespace']}/pods"),
        }
        for name, args in operations.items():
            try:
                data = self.client.get(*args)
                snapshot[name] = scrub_inventory(data, "pods") if name == "pods" else data
            except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
                snapshot[name] = {"items": []}
                snapshot["errors"].append({"component": name, "error": str(exc)[:500]})
        # Avoid collecting unrelated namespaces/pods into the calibration dataset.
        selected = {p["metadata"]["name"] for p in snapshot["pods"].get("items", [])}
        snapshot["pod_metrics"]["items"] = [p for p in snapshot["pod_metrics"].get("items", []) if p["metadata"]["name"] in selected]
        for url in c.get("app_metrics_urls", []):
            try:
                parts = urlsplit(url)
                if parts.query:
                    raise ValueError("Configure /metrics URL without query parameters")
                request_url = urlunsplit(parts._replace(query=urlencode({"after": self.cursors.get(url, 0)})))
                app = self.http_get(request_url)
                if app.get("pod_name") not in selected or app.get("namespace") != c["namespace"]:
                    raise ValueError("App identity does not match selected K3s pod inventory")
                snapshot["apps"].append(app)
                self.cursors[url] = app["sequence"]
            except (OSError, ValueError, KeyError) as exc:
                snapshot["errors"].append({"component": "app", "error": str(exc)[:500]})
        return snapshot


def collect(config: dict, output: Path, workload: dict, fixture: Path | None = None,
            samples: int = 12, interval: float = 15):
    if type(samples) is not int or not 1 <= samples <= 240 or not 2 <= interval <= 120 or samples * interval > 1800:
        raise ValueError("Use 1..240 samples, interval 2..120s, and <=1800s per session")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Collection output must be new/empty; preserve prior runs")
    source = "simulated" if fixture else "real"
    mode = "offline_fixture" if fixture else "live"
    context = {"source": source, "collection_mode": mode, "cluster_id": config["cluster_id"],
               "namespace": config["namespace"], "run_id": config["run_id"], "workload_sha256": digest(workload)}
    if fixture:
        data = json.loads(fixture.read_text(encoding="utf-8-sig"))
        if data.get("fixture") is not True:
            raise ValueError("Offline input must explicitly declare fixture=true")
        snapshots = iter(data["snapshots"])
        live = None
    else:
        live = LiveSource(config)
        snapshots = None
    output.mkdir(parents=True, exist_ok=True)
    parser = AppDeltaParser()
    count = rows_written = errors = 0
    write_records(output / "metrics.csv", [])
    with (output / "raw.jsonl").open("w", encoding="utf-8") as raw:
        for index in range(samples):
            if live:
                snapshot = live.snapshot()
            else:
                snapshot = next(snapshots, None)
                if snapshot is None:
                    break
            raw.write(json.dumps({"source": source, "collection_mode": mode, "snapshot": snapshot}) + "\n")
            raw.flush()
            records = kubernetes_records(snapshot, context)
            for app in snapshot.get("apps", []):
                try:
                    records.append(parser.parse(app, snapshot["observed_at_utc"], context))
                except (ValueError, KeyError) as exc:
                    snapshot.setdefault("errors", []).append({"component": "app_parser", "error": str(exc)})
            write_records(output / "metrics.csv", records, append=True)
            errors += len(snapshot.get("errors", []))
            count += 1
            rows_written += len(records)
            if snapshot.get("errors"):
                with (output / "errors.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"observed_at_utc": snapshot["observed_at_utc"], "errors": snapshot["errors"]}) + "\n")
            if live and index + 1 < samples:
                time.sleep(interval)
    metadata = {**context, "snapshots": count, "records": rows_written, "errors": errors,
                "workload": workload, "created_at_utc": utc_now(),
                "connected_to_real_cluster": bool(live and count and rows_written),
                "context": config.get("context") if live else None,
                "expected_node_names": config.get("expected_node_names") if live else None,
                "note": "Fixture development data; not evidence of Cluster A connectivity" if fixture else "Read-only collection; inspect errors and freshness before calibration"}
    (output / "session.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata
