#!/usr/bin/env bash
# Smoke test of the Docker image: analyses from the command line (without network: the solc
# versions are in the image) and one analysis job through the server.
#   bash scripts/docker_smoke.sh [image]        (default: fluxscope:local)
set -euo pipefail
IMAGE=${1:-fluxscope:local}
PORT=${PORT:-18765}   # host port; 8765 is often taken by a local `fluxscope serve`
NAME=fluxscope-smoke
# output under the repository (Docker Desktop and colima share the home directory, not /tmp)
out="$(cd "$(dirname "$0")/.." && pwd)/build/docker-smoke"
rm -rf "$out" && mkdir -p "$out"
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true' EXIT
json() { python3 -c "import json, sys; print(json.load(sys.stdin)$1)"; }

# 1. command line, offline: every bundled project, JSON and HTML report for each
for p in beefy-erc4626 kaia-yield metamorpho fixtures/receiver-vault fixtures/oz-erc4626; do
    n=$(basename "$p")
    docker run --rm --network none -v "$out:/out" "$IMAGE" \
        fluxscope analyze "corpus/$p" --quiet --json "/out/$n.json" --html "/out/$n.html"
    grep -q '"fluxscope/analysis"' "$out/$n.json"
    grep -q 'id="fluxscope-data"' "$out/$n.html"
    echo "cli: $p ok"
done

# 2. server: viewer, project list, one analysis job and its report
docker run -d --name "$NAME" -p "$PORT:8765" "$IMAGE" >/dev/null
base="http://127.0.0.1:$PORT"
for _ in $(seq 1 60); do curl -sf "$base/api/health" >/dev/null && break; sleep 1; done
# (responses go to files: `curl | grep -q` fails under pipefail when grep stops reading early)
curl -sf "$base/" -o "$out/index.html" && grep -q 'FluxScope' "$out/index.html"
curl -sf "$base/api/projects" -o "$out/projects.json" && grep -q 'beefy-erc4626' "$out/projects.json"
jid=$(curl -sf -X POST -H 'content-type: application/json' -d '{"project": "beefy-erc4626"}' "$base/api/jobs" | json '["id"]')
status=""
for _ in $(seq 1 300); do
    status=$(curl -sf "$base/api/jobs/$jid" | json '["status"]')
    [ "$status" = succeeded ] || [ "$status" = failed ] && break
    sleep 1
done
if [ "$status" != succeeded ]; then
    curl -s "$base/api/jobs/$jid"; echo; docker logs "$NAME" | tail -30
    echo "server job: $status" >&2; exit 1
fi
curl -sf "$base/api/jobs/$jid/report" -o "$out/job-report.html" && grep -q 'id="fluxscope-data"' "$out/job-report.html"
echo "server: job $jid ok"
echo "smoke test passed: $IMAGE"
