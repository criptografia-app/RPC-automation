#!/usr/bin/env bash
# ./deploy.sh init        create .env with random passwords and API keys
# ./deploy.sh             validate .env and deploy everything
# ./deploy.sh check       dry run (ansible --check --diff)
# ./deploy.sh test        call every enabled chain through the gateway
# ./deploy.sh endpoints   print your RPC URLs
# Extra arguments go to ansible-playbook, e.g. ./deploy.sh --tags edge
set -euo pipefail
cd "$(dirname "$0")"
ENV_FILE=${RPC_ENV_FILE:-.env}

envval() { python3 -c 'import inventory,sys; print(inventory.load_env().get(sys.argv[1], ""))' "$1"; }

need_env() {
  [ -f "$ENV_FILE" ] || { echo "No $ENV_FILE yet. Run: ./deploy.sh init" >&2; exit 1; }
  if ! python3 inventory.py --validate; then
    echo "Fix the items above in .env, then run ./deploy.sh again." >&2
    exit 1
  fi
}

need_ansible() {
  command -v ansible-playbook >/dev/null || {
    echo "ansible-playbook not found. Install it with: pipx install ansible-core  (or: pip install ansible-core)" >&2
    exit 1
  }
  ansible-galaxy collection list community.general >/dev/null 2>&1 \
    || ansible-galaxy collection install -r requirements.yml >/dev/null
}

gateway() {
  local edge; edge=$(envval EDGE_HOST)
  [ -n "$edge" ] || edge=$(envval NODE_HOSTS | cut -d, -f1 | tr -d ' ')
  echo "$edge"
}

base_url() {
  local domain; domain=$(envval RPC_DOMAIN)
  if [ "$(envval TLS_MODE)" = "selfsigned" ] || [ -z "$domain" ]; then
    echo "https://$(gateway)"
  else
    echo "https://$domain"
  fi
}

cmd=${1:-deploy}
case "$cmd" in
  init)
    [ -f "$ENV_FILE" ] && { echo "$ENV_FILE already exists; not overwriting it." >&2; exit 1; }
    ENV_FILE="$ENV_FILE" python3 - <<'EOF'
import os, re, secrets
s = open(".env.example").read()
s = re.sub(r"^NODE_RPC_PASSWORD=.*$", "NODE_RPC_PASSWORD=" + secrets.token_urlsafe(24), s, flags=re.M)
s = re.sub(r"^API_KEYS=.*$", "API_KEYS=" + secrets.token_urlsafe(24), s, flags=re.M)
s = re.sub(r"^GRAFANA_ADMIN_PASSWORD=.*$", "GRAFANA_ADMIN_PASSWORD=" + secrets.token_urlsafe(18), s, flags=re.M)
open(os.environ["ENV_FILE"], "w").write(s)
EOF
    chmod 600 "$ENV_FILE"
    echo "Created $ENV_FILE with random passwords and an API key."
    echo "Now set NODE_HOSTS, SSH_*, RPC_DOMAIN and ACME_EMAIL in $ENV_FILE, then run ./deploy.sh"
    ;;
  check)
    shift; need_env; need_ansible
    ansible-playbook site.yml --check --diff "$@"
    ;;
  test)
    need_env
    url=$(base_url); key=$(envval API_KEYS | cut -d, -f1); k=""
    [ "$(envval TLS_MODE)" = "selfsigned" ] && k="-k"
    fail=0
    for chain in $(envval CHAINS | tr ',' ' '); do
      if [ "$chain" = "counterparty" ]; then
        out=$(curl -s $k -m 20 "$url/$chain/$key/v2/" || true)
      else
        out=$(curl -s $k -m 20 -H 'content-type: application/json' \
          -d '{"jsonrpc":"1.0","id":"t","method":"getblockchaininfo","params":[]}' "$url/$chain/$key" || true)
      fi
      if echo "$out" | grep -q '"result"'; then
        echo "ok    $chain  $(echo "$out" | python3 -c 'import json,sys; r=json.load(sys.stdin)["result"]; print("blocks=%s headers=%s progress=%.4f" % (r.get("blocks"), r.get("headers"), r.get("verificationprogress", 0))) if isinstance(r, dict) and "blocks" in r else print("up")' 2>/dev/null)"
      else
        echo "FAIL  $chain  ${out:0:200}"; fail=1
      fi
    done
    code=$(curl -s $k -o /dev/null -w '%{http_code}' -m 10 -d '{}' "$url/bitcoin/not-a-valid-key" || true)
    [ "$code" = "404" ] && echo "ok    wrong API key is rejected (404)" || { echo "FAIL  wrong API key returned $code"; fail=1; }
    exit $fail
    ;;
  endpoints)
    need_env
    url=$(base_url)
    for chain in $(envval CHAINS | tr ',' ' '); do echo "$chain: $url/$chain/<API_KEY>"; done
    echo "grafana: $url/grafana/  (user admin)"
    ;;
  deploy|-*)
    [ "$cmd" = deploy ] && shift || true
    need_env; need_ansible
    ansible-playbook site.yml "$@"
    echo; "$0" endpoints
    ;;
  *)
    sed -n '2,8p' "$0"; exit 2
    ;;
esac
