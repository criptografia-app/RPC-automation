#!/usr/bin/env python3
"""Dynamic Ansible inventory built from .env (see .env.example).

  ./inventory.py --list       inventory JSON (what Ansible calls)
  ./inventory.py --validate   check .env and print problems
"""
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV_FILE = Path(os.environ.get("RPC_ENV_FILE", HERE / ".env"))

CHAINS = ("bitcoin", "litecoin", "dogecoin", "counterparty")
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{24,}$")
PLACEHOLDERS = {"", "change-me", "changeme"}
KNOWN = ("NODE_HOSTS", "NODE_PRIVATE_IPS", "EDGE_HOST", "EDGE_PRIVATE_IP", "SSH_USER", "SSH_PORT",
         "SSH_KEY", "SSH_PASSWORD", "SUDO_PASSWORD", "CHAINS", "PRUNE_MB", "NODE_RPC_USER",
         "NODE_RPC_PASSWORD", "RPC_DOMAIN", "ACME_EMAIL", "TLS_MODE", "CLOUDFLARE_DNS_API_TOKEN",
         "API_KEYS", "GRAFANA_ADMIN_PASSWORD", "FIREWALL", "FORCE_IPV4")


def load_env():
    env = {}
    if ENV_FILE.exists():
        for raw in ENV_FILE.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            if key.startswith("export "):
                key = key[len("export "):].strip()
            val = val.strip()
            if len(val) >= 2 and val[0] in "'\"" and val[-1] == val[0]:
                val = val[1:-1]
            else:
                val = val.split(" #", 1)[0].strip()
            env[key] = val
    for key in set(env) | set(KNOWN):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def items(value):
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def flag(value, default=False):
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def build(env):
    nodes = items(env.get("NODE_HOSTS"))
    privates = items(env.get("NODE_PRIVATE_IPS"))
    edge = env.get("EDGE_HOST", "").strip()
    chains = [c.lower() for c in items(env.get("CHAINS", "bitcoin"))]

    conn = {"ansible_user": env.get("SSH_USER") or "root",
            "ansible_port": int(env.get("SSH_PORT") or 22)}
    if env.get("SSH_KEY"):
        conn["ansible_ssh_private_key_file"] = os.path.expanduser(env["SSH_KEY"])
    if env.get("SSH_PASSWORD"):
        conn["ansible_password"] = env["SSH_PASSWORD"]
    if env.get("SUDO_PASSWORD"):
        conn["ansible_become_password"] = env["SUDO_PASSWORD"]

    hostvars, node_names = {}, []
    for i, host in enumerate(nodes):
        name = "node%d" % (i + 1)
        node_names.append(name)
        hostvars[name] = dict(conn, ansible_host=host,
                              backend_ip=privates[i] if i < len(privates) else host)

    # Single-server setup: the gateway runs on the first node.
    if not edge or edge in nodes:
        edge_name = node_names[nodes.index(edge)] if edge in nodes else (node_names[0] if node_names else None)
    else:
        edge_name = "edge"
        hostvars[edge_name] = dict(conn, ansible_host=edge,
                                   backend_ip=env.get("EDGE_PRIVATE_IP") or edge)
    edge_backend = hostvars[edge_name]["backend_ip"] if edge_name else ""

    for name in node_names:
        # Same machine as the gateway: RPC never leaves loopback.
        hostvars[name]["colocated_edge"] = name == edge_name

    full_bitcoin = "counterparty" in chains
    shared = {
        "rpc_chains": chains,
        "prune_mb": int(env.get("PRUNE_MB") or 0),
        "bitcoin_full": full_bitcoin,
        "node_rpc_user": env.get("NODE_RPC_USER") or "rpc",
        "node_rpc_password": env.get("NODE_RPC_PASSWORD", ""),
        "rpc_domain": env.get("RPC_DOMAIN", "").strip(),
        "acme_email": env.get("ACME_EMAIL", "").strip(),
        "tls_mode": (env.get("TLS_MODE") or "acme").strip().lower(),
        "cloudflare_dns_api_token": env.get("CLOUDFLARE_DNS_API_TOKEN", ""),
        "api_keys": items(env.get("API_KEYS")),
        "grafana_admin_password": env.get("GRAFANA_ADMIN_PASSWORD", ""),
        "firewall_enabled": flag(env.get("FIREWALL"), True),
        "force_ipv4": flag(env.get("FORCE_IPV4"), False),
        "ssh_port": conn["ansible_port"],
        "edge_backend_ip": edge_backend,
        "node_hosts": node_names,
    }
    return {
        "_meta": {"hostvars": hostvars},
        "all": {"children": ["nodes", "edge"], "vars": shared},
        "nodes": {"hosts": node_names},
        "edge": {"hosts": [edge_name] if edge_name else []},
    }


def problems(env):
    out = []
    nodes = items(env.get("NODE_HOSTS"))
    if not nodes or nodes == ["203.0.113.10"]:
        out.append("NODE_HOSTS: set your server IPs (203.0.113.10 is the example).")
    privates = items(env.get("NODE_PRIVATE_IPS"))
    if privates and len(privates) != len(nodes):
        out.append("NODE_PRIVATE_IPS: needs one entry per NODE_HOSTS entry, in the same order.")
    key = env.get("SSH_KEY", "")
    if key and not Path(os.path.expanduser(key)).exists():
        out.append("SSH_KEY: %s not found." % key)
    chains = [c.lower() for c in items(env.get("CHAINS", "bitcoin"))]
    bad = [c for c in chains if c not in CHAINS]
    if bad or not chains:
        out.append("CHAINS: unknown %s. Use any of %s." % (", ".join(bad) or "(empty)", ", ".join(CHAINS)))
    if "counterparty" in chains and "bitcoin" not in chains:
        out.append("CHAINS: counterparty needs bitcoin in the list too.")
    try:
        prune = int(env.get("PRUNE_MB") or 0)
        if 0 < prune < 550:
            out.append("PRUNE_MB: 0 (full node) or at least 550.")
    except ValueError:
        out.append("PRUNE_MB: must be a number.")
    pw = env.get("NODE_RPC_PASSWORD", "")
    if pw in PLACEHOLDERS or len(pw) < 16 or not re.fullmatch(r"[A-Za-z0-9_-]+", pw):
        out.append("NODE_RPC_PASSWORD: at least 16 letters/digits/-/_ (./deploy.sh init generates one).")
    keys = items(env.get("API_KEYS"))
    if not keys or any(not KEY_RE.match(k) for k in keys):
        out.append("API_KEYS: each key needs 24+ letters/digits/-/_ (./deploy.sh init generates one).")
    gp = env.get("GRAFANA_ADMIN_PASSWORD", "")
    if gp in PLACEHOLDERS or len(gp) < 12:
        out.append("GRAFANA_ADMIN_PASSWORD: at least 12 characters.")
    tls = (env.get("TLS_MODE") or "acme").lower()
    if tls not in ("acme", "selfsigned"):
        out.append("TLS_MODE: acme or selfsigned.")
    if tls == "acme":
        if env.get("RPC_DOMAIN", "") in ("", "rpc.example.com"):
            out.append("RPC_DOMAIN: set your domain (or TLS_MODE=selfsigned to test without one).")
        if env.get("ACME_EMAIL", "") in ("", "you@example.com"):
            out.append("ACME_EMAIL: set a real email for Let's Encrypt.")
    return out


def main():
    if not ENV_FILE.exists() and "RPC_ENV_FILE" not in os.environ:
        print("No .env found. Run ./deploy.sh init first.", file=sys.stderr)
    env = load_env()
    if "--validate" in sys.argv:
        errs = problems(env)
        for e in errs:
            print("  - " + e, file=sys.stderr)
        sys.exit(1 if errs else 0)
    if "--host" in sys.argv:
        print("{}")
        return
    print(json.dumps(build(env), indent=2))


if __name__ == "__main__":
    main()
