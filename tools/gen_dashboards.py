#!/usr/bin/env python3
"""Generates the Grafana dashboards in roles/edge/files/dashboards/.

Edit here, run `python3 tools/gen_dashboards.py`, commit the JSON.
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "roles" / "edge" / "files" / "dashboards"
DS = {"type": "prometheus", "uid": "prometheus"}
# Traefik labels routers "bitcoin@file"; expose that as chain="bitcoin", skip Grafana's own route.
R = 'router=~".+@file", router!="grafana@file"'
CHAIN = 'label_replace({q}, "chain", "$1", "router", "(.+)@file")'


class Board:
    def __init__(self):
        self.panels, self.y, self.x, self.row_h, self.next_id = [], 0, 0, 0, 1

    def _place(self, w, h):
        if self.x + w > 24:
            self.y += self.row_h
            self.x, self.row_h = 0, 0
        pos = {"x": self.x, "y": self.y, "w": w, "h": h}
        self.x += w
        self.row_h = max(self.row_h, h)
        return pos

    def row(self, title):
        if self.x:
            self.y += self.row_h
        self.x, self.row_h = 0, 0
        self.panels.append({"id": self.next_id, "type": "row", "title": title, "collapsed": False,
                            "gridPos": {"x": 0, "y": self.y, "w": 24, "h": 1}, "panels": []})
        self.next_id += 1
        self.y += 1

    def add(self, kind, title, targets, w=12, h=8, unit=None, desc=None, **opts):
        p = {"id": self.next_id, "type": kind, "title": title, "datasource": DS,
             "gridPos": self._place(w, h),
             "targets": [{"refId": chr(65 + i), "expr": e, "legendFormat": l, "datasource": DS}
                         for i, (e, l) in enumerate(targets)],
             "fieldConfig": {"defaults": {}, "overrides": []}, "options": {}}
        if unit:
            p["fieldConfig"]["defaults"]["unit"] = unit
        if desc:
            p["description"] = desc
        if kind == "stat":
            named = any(l for _, l in targets)
            p["options"] = {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "background",
                            "graphMode": "area", "textMode": "value_and_name" if named else "value"}
            p["fieldConfig"]["defaults"]["thresholds"] = opts.get("thresholds", {
                "mode": "absolute", "steps": [{"color": "green", "value": None}]})
        if kind == "timeseries":
            p["fieldConfig"]["defaults"]["custom"] = {"lineWidth": 2, "fillOpacity": 8, "showPoints": "never"}
            p["options"] = {"legend": {"displayMode": "table", "placement": "bottom",
                                       "calcs": ["lastNotNull", "max"]},
                            "tooltip": {"mode": "multi", "sort": "desc"}}
        if kind == "table":
            p["options"] = {"showHeader": True}
            p["transformations"] = opts.get("transformations", [])
            p["fieldConfig"]["overrides"] = opts.get("overrides", [])
            for t in p["targets"]:
                t.update({"format": "table", "instant": True})
        self.panels.append(p)
        self.next_id += 1

    def save(self, uid, title, description, variables=()):
        board = {"uid": uid, "title": title, "description": description, "tags": ["rpc-automation"],
                 "schemaVersion": 39, "time": {"from": "now-6h", "to": "now"}, "refresh": "30s",
                 "panels": self.panels, "templating": {"list": list(variables)},
                 "links": [{"title": "RPC dashboards", "type": "dashboards", "tags": ["rpc-automation"],
                            "asDropdown": True, "includeVars": True, "keepTime": True}]}
        (OUT / (uid + ".json")).write_text(json.dumps(board, indent=1) + "\n")


def var(name, query, label, multi=True):
    return {"name": name, "label": label, "type": "query", "datasource": DS,
            "query": {"query": query, "refId": name}, "definition": query, "refresh": 2,
            "includeAll": True, "multi": multi, "current": {"text": "All", "value": "$__all"}, "sort": 1}


RED = {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "green", "value": 1}]}


def overview():
    b = Board()
    b.add("stat", "Chains answering RPC", [("sum(chain_rpc_up)", "up")], w=4, h=5, thresholds=RED)
    b.add("stat", "Requests / s", [(f"sum(rate(traefik_router_requests_total{{{R}}}[5m]))", "req/s")],
          w=4, h=5, unit="reqps")
    b.add("stat", "Errors (5xx)", [(f'sum(rate(traefik_router_requests_total{{{R}, code=~"5.."}}[5m])) / '
                                   f"clamp_min(sum(rate(traefik_router_requests_total{{{R}}}[5m])), 1e-9)", "5xx")],
          w=4, h=5, unit="percentunit",
          thresholds={"mode": "absolute", "steps": [{"color": "green", "value": None},
                                                     {"color": "orange", "value": 0.01},
                                                     {"color": "red", "value": 0.05}]})
    b.add("stat", "p95 latency", [(f"histogram_quantile(0.95, sum by (le) (rate("
                                   f"traefik_router_request_duration_seconds_bucket{{{R}}}[5m])))", "p95")],
          w=4, h=5, unit="s")
    b.add("stat", "Bad API keys / s", [('sum(rate(traefik_entrypoint_requests_total{entrypoint="websecure", code="404"}[5m]))',
                                             "404")], w=4, h=5, unit="reqps",
          desc="Requests that matched no chain route: wrong or revoked API key, or a bad path.")
    b.add("stat", "Servers up", [('sum(up{job="node"})', "up")], w=4, h=5, thresholds=RED)
    b.add("table", "Chains", [
        ("max by (chain) (chain_block_height)", ""),
        ("max by (chain) (chain_headers)", ""),
        ("max by (chain) (chain_sync_progress)", ""),
        ("sum by (chain) (chain_peers)", ""),
        ("min by (chain) (chain_rpc_up)", "")], w=24, h=7,
        transformations=[{"id": "merge"},
                         {"id": "organize", "options": {
                             "excludeByName": {"Time": True},
                             "renameByName": {"Value #A": "Height", "Value #B": "Headers",
                                              "Value #C": "Synced", "Value #D": "Peers",
                                              "Value #E": "RPC up", "chain": "Chain"}}}],
        overrides=[{"matcher": {"id": "byName", "options": "Synced"},
                    "properties": [{"id": "unit", "value": "percentunit"}, {"id": "decimals", "value": 2}]},
                   {"matcher": {"id": "byName", "options": "RPC up"},
                    "properties": [{"id": "mappings", "value": [{"type": "value", "options": {
                        "0": {"text": "down", "color": "red"}, "1": {"text": "up", "color": "green"}}}]},
                                   {"id": "custom.cellOptions", "value": {"type": "color-text"}}]}])
    b.add("timeseries", "Requests per second by chain",
          [(CHAIN.format(q=f"sum by (router) (rate(traefik_router_requests_total{{{R}}}[5m]))"), "{{chain}}")],
          unit="reqps")
    b.add("timeseries", "Blocks behind the best header",
          [("max by (chain) (chain_headers - chain_block_height)", "{{chain}}")], unit="none",
          desc="0 when the node is in sync.")
    b.save("rpc-overview", "RPC overview", "Health of every chain, the gateway and the servers at a glance.")


def chain_board(chain):
    """Everything about one chain: node status, sync, mempool, its gateway route, its servers."""
    b = Board()
    c = f'chain="{chain}"'
    route = f'router="{chain}@file"'
    is_coin = chain != "counterparty"
    if is_coin:
        b.row("Status")
        b.add("stat", "RPC up", [(f"min(chain_rpc_up{{{c}}})", "")], w=3, h=4, thresholds=RED,
              unit="bool_yes_no")
        b.add("stat", "RPC response", [(f"max(chain_rpc_seconds{{{c}}})", "")], w=3, h=4, unit="s",
              desc="How long the node took to answer getblockchaininfo. Seconds are normal during initial sync.",
              thresholds={"mode": "absolute", "steps": [{"color": "green", "value": None},
                                                         {"color": "orange", "value": 1},
                                                         {"color": "red", "value": 10}]})
        b.add("stat", "Height", [(f"max(chain_block_height{{{c}}})", "")], w=3, h=4, unit="none")
        b.add("stat", "Headers", [(f"max(chain_headers{{{c}}})", "")], w=3, h=4, unit="none")
        b.add("stat", "Synced", [(f"min(chain_sync_progress{{{c}}})", "")], w=3, h=4, unit="percentunit",
              thresholds={"mode": "absolute", "steps": [{"color": "orange", "value": None},
                                                         {"color": "green", "value": 0.9999}]})
        b.add("stat", "Peers", [(f"sum(chain_peers{{{c}}})", "")], w=3, h=4, unit="none",
              thresholds={"mode": "absolute", "steps": [{"color": "red", "value": None},
                                                         {"color": "orange", "value": 1},
                                                         {"color": "green", "value": 4}]})
        b.add("stat", "Mempool", [(f"sum(chain_mempool_tx{{{c}}})", "")], w=3, h=4, unit="none")
        b.add("stat", "On disk", [(f"max(chain_size_on_disk_bytes{{{c}}})", "")], w=3, h=4, unit="bytes")
        b.row("Sync")
        b.add("timeseries", "Block height vs headers", [
            (f"chain_block_height{{{c}}}", "blocks {{host}}"),
            (f"chain_headers{{{c}}}", "headers {{host}}")], unit="none")
        b.add("timeseries", "Sync progress", [(f"chain_sync_progress{{{c}}}", "{{host}}")], unit="percentunit")
        b.add("timeseries", "Blocks behind", [(f"chain_headers{{{c}}} - chain_block_height{{{c}}}", "{{host}}")],
              unit="none", desc="0 when in sync.")
        b.add("timeseries", "Blocks validated per hour", [(f"increase(chain_block_height{{{c}}}[1h])", "{{host}}")],
              unit="none")
        b.row("Network and mempool")
        b.add("timeseries", "Peers", [(f"chain_peers{{{c}}}", "{{host}}")], w=8, unit="none")
        b.add("timeseries", "Mempool transactions", [(f"chain_mempool_tx{{{c}}}", "{{host}}")], w=8, unit="none")
        b.add("timeseries", "Mempool size", [(f"chain_mempool_bytes{{{c}}}", "{{host}}")], w=8, unit="bytes")
    b.row("Gateway: /%s/" % chain)
    b.add("stat", "Requests / s", [(f"sum(rate(traefik_router_requests_total{{{route}}}[5m])) or vector(0)", "")],
          w=6, h=4, unit="reqps")
    b.add("stat", "p95 latency", [(f"histogram_quantile(0.95, sum by (le) (rate("
                                   f"traefik_router_request_duration_seconds_bucket{{{route}}}[5m])))", "")],
          w=6, h=4, unit="s")
    b.add("stat", "Error rate (5xx)", [(f'(sum(rate(traefik_router_requests_total{{{route}, code=~"5.."}}[5m])) or vector(0))'
                                        f" / clamp_min(sum(rate(traefik_router_requests_total{{{route}}}[5m])), 1e-9)", "")],
          w=6, h=4, unit="percentunit",
          thresholds={"mode": "absolute", "steps": [{"color": "green", "value": None},
                                                     {"color": "orange", "value": 0.01},
                                                     {"color": "red", "value": 0.05}]})
    b.add("stat", "Requests (24h)", [(f"sum(increase(traefik_router_requests_total{{{route}}}[24h])) or vector(0)", "")],
          w=6, h=4, unit="short")
    b.add("timeseries", "Requests per second by status",
          [(f"sum by (code) (rate(traefik_router_requests_total{{{route}}}[5m]))", "{{code}}")], unit="reqps")
    b.add("timeseries", "Latency", [
        (f"histogram_quantile({q}, sum by (le) (rate(traefik_router_request_duration_seconds_bucket{{{route}}}[5m])))",
         "p" + p) for q, p in (("0.5", "50"), ("0.95", "95"), ("0.99", "99"))], unit="s")
    b.add("timeseries", "Rate-limited (429) and backend errors (5xx)", [
        (f'sum(rate(traefik_router_requests_total{{{route}, code="429"}}[5m])) or vector(0)', "429"),
        (f'sum(rate(traefik_router_requests_total{{{route}, code=~"5.."}}[5m])) or vector(0)', "5xx")], unit="reqps")
    b.add("timeseries", "Bandwidth", [
        (f"sum(rate(traefik_router_requests_bytes_total{{{route}}}[5m])) or vector(0)", "in"),
        (f"sum(rate(traefik_router_responses_bytes_total{{{route}}}[5m])) or vector(0)", "out")], unit="Bps")
    b.row("Servers running %s" % chain)
    on = (f' and on (host) group by (host) (chain_rpc_up{{{c}}})' if is_coin else "")
    b.add("timeseries", "CPU busy", [(f'(1 - avg by (host) (rate(node_cpu_seconds_total{{mode="idle"}}[5m]))){on}',
                                      "{{host}}")], w=8, unit="percentunit")
    b.add("timeseries", "Memory used", [(f"(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes){on}",
                                         "{{host}}")], w=8, unit="percentunit")
    b.add("timeseries", "Disk used (/)", [(f'(1 - node_filesystem_avail_bytes{{mountpoint="/"}} / '
                                           f'node_filesystem_size_bytes{{mountpoint="/"}}){on}', "{{host}}")],
          w=8, unit="percentunit")
    b.save(f"rpc-{chain}", f"RPC · {chain}", f"Everything about {chain}: node, sync, mempool, gateway traffic, servers.")


def hosts():
    b = Board()
    h = 'host=~"$host"'
    b.add("timeseries", "CPU busy", [(f'1 - avg by (host) (rate(node_cpu_seconds_total{{{h}, mode="idle"}}[5m]))',
                                      "{{host}}")], w=8, unit="percentunit")
    b.add("timeseries", "Load (1m) per core", [(f"node_load1{{{h}}} / on (host) count by (host) "
                                                f'(node_cpu_seconds_total{{{h}, mode="idle"}})', "{{host}}")],
          w=8, unit="none")
    b.add("timeseries", "Memory used", [(f"1 - node_memory_MemAvailable_bytes{{{h}}} / node_memory_MemTotal_bytes{{{h}}}",
                                         "{{host}}")], w=8, unit="percentunit")
    fs = f'{h}, fstype!~"tmpfs|overlay|squashfs"'
    b.add("timeseries", "Disk used", [(f"1 - node_filesystem_avail_bytes{{{fs}}} / node_filesystem_size_bytes{{{fs}}}",
                                       "{{host}} {{mountpoint}}")], w=8, unit="percentunit")
    b.add("timeseries", "Disk free", [(f"node_filesystem_avail_bytes{{{fs}}}", "{{host}} {{mountpoint}}")], w=8,
          unit="bytes")
    b.add("timeseries", "Days until disk full (24h trend)",
          [(f'(node_filesystem_avail_bytes{{{fs}}} / clamp_min(-deriv(node_filesystem_avail_bytes{{{fs}}}[24h]), 1)) / 86400',
            "{{host}} {{mountpoint}}")], w=8, unit="d",
          desc="Free space divided by the shrink rate over the last day. Chains only grow.")
    b.add("timeseries", "Disk I/O", [
        (f"sum by (host) (rate(node_disk_read_bytes_total{{{h}}}[5m]))", "read {{host}}"),
        (f"sum by (host) (rate(node_disk_written_bytes_total{{{h}}}[5m]))", "write {{host}}")], unit="Bps")
    b.add("timeseries", "Network", [
        (f'sum by (host) (rate(node_network_receive_bytes_total{{{h}, device!~"lo|ve-.*|veth.*"}}[5m]))', "in {{host}}"),
        (f'sum by (host) (rate(node_network_transmit_bytes_total{{{h}, device!~"lo|ve-.*|veth.*"}}[5m]))', "out {{host}}")],
          unit="Bps")
    b.save("rpc-hosts", "RPC hosts", "CPU, memory, disk and network for every server.",
           [var("host", 'label_values(node_uname_info, host)', "Server")])


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.json"):
        old.unlink()
    for build in (overview, hosts):
        build()
    for chain in ("bitcoin", "litecoin", "dogecoin", "counterparty"):
        chain_board(chain)
    print("\n".join(sorted(p.name for p in OUT.glob("*.json"))))
