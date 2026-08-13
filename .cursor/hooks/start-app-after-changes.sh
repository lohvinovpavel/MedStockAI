#!/usr/bin/env bash
# stop + subagentStop: restart the local MedStockAI dev stack after code changes.
# Always prints {} on stdout (no followup_message — this starts servers, not an agent loop).
# Logs go to /tmp/medstock-dev/hook-start.log.

export PATH="/usr/local/bin:/usr/bin:/bin:${PATH:-}"
# Isolated tests (and some hosts) set these; they make `git status` see the wrong tree.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT" || {
  printf '%s\n' '{}'
  exit 0
}

DEV="/tmp/medstock-dev"
LOG="$DEV/hook-start.log"
LOCK="$DEV/hook-start.lock"
STAMP="$DEV/hook-start.last"
DEBOUNCE_SECS=45
mkdir -p "$DEV"

log() {
  printf '%s %s\n' "$(date -Iseconds)" "$*" >>"$LOG"
}

emit_ok() {
  printf '%s\n' '{}'
}

# Keep stdout for the stop payload only. Everything else (including python
# tracebacks) goes to the log.
exec 3>&1
exec >>"$LOG" 2>&1

trap 'emit_ok >&3' EXIT

INPUT="$(cat || true)"

# Parse stdin without assuming jq. Unknown / invalid input is not fatal.
# stop: {status, loop_count}
# subagentStop: {subagent_type, status, loop_count, ...} — never emit followup_message.
parse_stdin() {
  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "$INPUT" | python3 -c '
import json, sys
raw = sys.stdin.read()
try:
    data = json.loads(raw) if raw.strip() else {}
except Exception:
    data = {}
if not isinstance(data, dict):
    data = {}
print(data.get("status") or "")
print(data.get("loop_count", 0))
print(data.get("subagent_type") or "")
'
    return
  fi
  if command -v jq >/dev/null 2>&1; then
    printf '%s\n' "$INPUT" | jq -r '.status // empty'
    printf '%s\n' "$INPUT" | jq -r '.loop_count // 0'
    printf '%s\n' "$INPUT" | jq -r '.subagent_type // empty'
    return
  fi
  printf '%s\n' ""
  printf '%s\n' "0"
  printf '%s\n' ""
}

mapfile -t _parsed < <(parse_stdin)
STATUS="${_parsed[0]:-}"
LOOP_COUNT="${_parsed[1]:-0}"
SUBAGENT_TYPE="${_parsed[2]:-}"
if [ -n "$SUBAGENT_TYPE" ]; then
  EVENT="subagentStop"
else
  EVENT="stop"
fi
log "hook start event=${EVENT} status=${STATUS:-unknown} subagent_type=${SUBAGENT_TYPE:-none} loop_count=${LOOP_COUNT} root=$ROOT"

# Task explore agents do not edit app code; leftover dirty files from earlier
# turns must not restart the stack.
if [ "$SUBAGENT_TYPE" = "explore" ] || [ "$SUBAGENT_TYPE" = "cursor-guide" ]; then
  log "skip: subagent_type=${SUBAGENT_TYPE}"
  exit 0
fi
if [ "$STATUS" = "aborted" ]; then
  log "skip: status=aborted"
  exit 0
fi

# Skip chat-only / docs-only turns: require dirty or untracked files under
# web/, services/, shared/, scripts/ that are not solely markdown.
relevant_changed() {
  python3 - "$ROOT" <<'PY'
import subprocess, sys

root = sys.argv[1]
prefixes = ("web/", "services/", "shared/", "scripts/")
try:
    out = subprocess.check_output(
        ["git", "-c", "safe.directory=*", "status", "--porcelain"],
        cwd=root,
        text=True,
        stderr=subprocess.PIPE,
    )
except Exception as exc:
    sys.stderr.write(f"git status failed: {exc}\n")
    sys.exit(2)

paths = []
for line in out.splitlines():
    if len(line) < 4:
        continue
    path = line[3:]
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    path = path.strip().strip('"')
    if path.startswith(prefixes) or path in {p.rstrip("/") for p in prefixes}:
        paths.append(path)

if not paths:
    sys.exit(1)

non_md = [
    p for p in paths
    if not (p.endswith(".md") or p.endswith(".markdown"))
]
sys.exit(0 if non_md else 1)
PY
}

relevant_changed
_rc=$?
if [ "$_rc" -eq 1 ]; then
  log "skip: no relevant dirty/untracked files under web/ services/ shared/ scripts/ (or only markdown)"
  exit 0
fi
if [ "$_rc" -eq 2 ]; then
  log "git status failed; starting anyway (fail-open)"
fi

# stop + subagentStop can fire close together; do not double-start.
_now="$(date +%s)"
if [ -f "$STAMP" ]; then
  _prev="$(tr -dc '0-9' <"$STAMP" || true)"
  if [ -n "${_prev:-}" ] && [ $((_now - _prev)) -lt "$DEBOUNCE_SECS" ]; then
    log "skip: debounce (${DEBOUNCE_SECS}s since last start)"
    exit 0
  fi
fi
printf '%s\n' "$_now" >"$STAMP"

inventory_changed() {
  python3 - "$ROOT" <<'PY'
import subprocess, sys

root = sys.argv[1]
prefixes = ("services/inventory/", "shared/")
try:
    out = subprocess.check_output(
        ["git", "-c", "safe.directory=*", "status", "--porcelain"],
        cwd=root,
        text=True,
        stderr=subprocess.DEVNULL,
    )
except Exception:
    sys.exit(1)

for line in out.splitlines():
    if len(line) < 4:
        continue
    path = line[3:]
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    path = path.strip().strip('"')
    if path.startswith(prefixes) or path in ("services/inventory", "shared"):
        sys.exit(0)
sys.exit(1)
PY
}

web_changed() {
  python3 - "$ROOT" <<'PY'
import subprocess, sys
root = sys.argv[1]
try:
    out = subprocess.check_output(
        ["git", "-c", "safe.directory=*", "status", "--porcelain"],
        cwd=root,
        text=True,
        stderr=subprocess.DEVNULL,
    )
except Exception:
    sys.exit(1)
for line in out.splitlines():
    if len(line) < 4:
        continue
    path = line[3:]
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    path = path.strip().strip('"')
    if path.startswith("web/") or path == "web":
        if not (path.endswith(".md") or path.endswith(".markdown")):
            sys.exit(0)
sys.exit(1)
PY
}

dotenv_get() {
  local file="$1" key="$2"
  python3 - "$file" "$key" <<'PY'
import sys
path, key = sys.argv[1], sys.argv[2]
try:
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
except OSError:
    sys.exit(0)
for raw in text.splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    if k.strip() != key:
        continue
    v = v.strip()
    if (len(v) >= 2) and ((v[0] == v[-1]) and v[0] in ("'", '"')):
        v = v[1:-1]
    if v:
        sys.stdout.write(v)
    break
PY
}

http_code() {
  local url="$1"
  curl -sS -o /dev/null -w '%{http_code}' --max-time 2 "$url" 2>/dev/null || printf '%s' "000"
}

wait_http() {
  local url="$1" tries="${2:-20}" delay="${3:-0.5}"
  local i code
  for i in $(seq 1 "$tries"); do
    code="$(http_code "$url")"
    if [ "$code" = "200" ]; then
      log "health ok $url -> 200"
      return 0
    fi
    sleep "$delay"
  done
  log "health miss $url last=$(http_code "$url")"
  return 1
}

# Kill only the TCP listener on this port (and its direct children), not
# unrelated apps on other ports.
kill_port() {
  local port="$1"
  local pids=""
  if command -v lsof >/dev/null 2>&1; then
    pids="$(lsof -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  fi
  if [ -z "$pids" ] && command -v fuser >/dev/null 2>&1; then
    pids="$(fuser "${port}/tcp" 2>/dev/null || true)"
  fi
  if [ -z "$pids" ]; then
    log "port $port already free"
    return 0
  fi
  log "killing listeners on $port: $pids"
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  sleep 0.4
  local still=""
  if command -v lsof >/dev/null 2>&1; then
    still="$(lsof -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  fi
  if [ -n "$still" ]; then
    log "force-killing remaining on $port: $still"
    # shellcheck disable=SC2086
    kill -9 $still 2>/dev/null || true
    sleep 0.2
  fi
}

ensure_postgres() {
  if ! command -v docker >/dev/null 2>&1; then
    log "docker not on PATH; skipping postgres ensure"
    return 0
  fi
  if docker inspect medstock-postgres >/dev/null 2>&1; then
    local status
    status="$(docker inspect -f '{{.State.Status}}' medstock-postgres 2>/dev/null || true)"
    if [ "$status" = "running" ]; then
      log "postgres: medstock-postgres already running"
      return 0
    fi
    log "postgres: starting existing medstock-postgres (status=$status)"
    docker start medstock-postgres >/dev/null || log "postgres: docker start failed"
    return 0
  fi
  log "postgres: creating medstock-postgres on 127.0.0.1:5432"
  docker run -d --name medstock-postgres \
    -e POSTGRES_USER=medstock \
    -e POSTGRES_PASSWORD=medstock \
    -e POSTGRES_DB=medstock \
    -p 127.0.0.1:5432:5432 \
    postgres:16 >/dev/null || log "postgres: docker run failed"
}

load_runtime_env() {
  DATABASE_URL="$(dotenv_get "$ROOT/.env" DATABASE_URL)"
  if [ -z "${DATABASE_URL:-}" ]; then
    DATABASE_URL="postgresql+psycopg://medstock:medstock@127.0.0.1:5432/medstock"
  fi
  export DATABASE_URL

  JWT_PUBLIC_KEY=""
  if [ -s "$DEV/jwt-public.pem" ]; then
    JWT_PUBLIC_KEY="$(cat "$DEV/jwt-public.pem")"
  else
    JWT_PUBLIC_KEY="$(dotenv_get "$ROOT/.env" JWT_PUBLIC_KEY)"
  fi
  if [ -n "${JWT_PUBLIC_KEY:-}" ]; then
    export JWT_PUBLIC_KEY
    log "jwt: loaded (pem or .env)"
  else
    unset JWT_PUBLIC_KEY
    log "jwt: missing (analogue/inventory may 401)"
  fi

  # Local convention: do not pass GEMINI_API_KEY unless .env has a real value.
  unset GEMINI_API_KEY GEMINI_API_KEY_FILE
  local gemini
  gemini="$(dotenv_get "$ROOT/.env" GEMINI_API_KEY)"
  if [ -n "${gemini:-}" ]; then
    export GEMINI_API_KEY="$gemini"
    log "gemini: using key from .env"
  else
    log "gemini: unset"
  fi

  # Optional override of Settings.gemini_model. Never hardcode a model id here.
  unset GEMINI_MODEL
  local gemini_model
  gemini_model="$(dotenv_get "$ROOT/.env" GEMINI_MODEL)"
  if [ -n "${gemini_model:-}" ]; then
    export GEMINI_MODEL="$gemini_model"
    log "gemini: GEMINI_MODEL from .env"
  else
    log "gemini: GEMINI_MODEL unset (Settings.gemini_model default)"
  fi
}

ensure_web_token() {
  if [ -f "$ROOT/web/.env.local" ] && grep -q '^NEXT_PUBLIC_DEV_TOKEN=' "$ROOT/web/.env.local" 2>/dev/null; then
    log "web/.env.local already has NEXT_PUBLIC_DEV_TOKEN"
    return 0
  fi
  if [ -s "$DEV/token.txt" ]; then
    printf 'NEXT_PUBLIC_DEV_TOKEN=%s\n' "$(tr -d '\n' <"$DEV/token.txt")" >>"$ROOT/web/.env.local"
    log "web/.env.local: appended NEXT_PUBLIC_DEV_TOKEN from $DEV/token.txt"
  else
    log "web token helper missing; not writing .env.local"
  fi
}

python_import_check() {
  local py="$ROOT/.venv/bin/python"
  if [ ! -x "$py" ]; then
    py="$(command -v python3 || true)"
  fi
  if [ -z "$py" ]; then
    log "python import check skipped (no interpreter)"
    return 0
  fi
  PYTHONPATH="$ROOT/shared:$ROOT/services/analogue" "$py" -c "from app.main import app" \
    && log "python import: analogue app.main ok" \
    || log "python import: analogue app.main failed (continuing)"
}

web_tsc_check() {
  if [ ! -d "$ROOT/web/node_modules" ]; then
    log "tsc skipped (no web/node_modules)"
    return 0
  fi
  if ! web_changed; then
    log "tsc skipped (web/ unchanged)"
    return 0
  fi
  # Lightweight; never fail the hook on tsc warnings/errors.
  if command -v timeout >/dev/null 2>&1; then
    (cd "$ROOT/web" && timeout 20 npx tsc --noEmit) \
      && log "tsc: ok" \
      || log "tsc: warnings/errors ignored"
  else
    (cd "$ROOT/web" && npx tsc --noEmit) \
      && log "tsc: ok" \
      || log "tsc: warnings/errors ignored"
  fi
}

# nohup + disown so uvicorn/next survive when the hook process exits.
start_analogue() {
  kill_port 8002
  if [ -z "${GEMINI_API_KEY:-}" ]; then
    unset GEMINI_API_KEY GEMINI_API_KEY_FILE
  fi
  (
    cd "$ROOT/services/analogue" || exit 1
    export PYTHONPATH="$ROOT/shared:$ROOT/services/analogue"
    export DATABASE_URL
    if [ -n "${JWT_PUBLIC_KEY:-}" ]; then
      export JWT_PUBLIC_KEY
    fi
    if [ -z "${GEMINI_API_KEY:-}" ]; then
      unset GEMINI_API_KEY GEMINI_API_KEY_FILE
    fi
    if [ -n "${GEMINI_MODEL:-}" ]; then
      export GEMINI_MODEL
    else
      unset GEMINI_MODEL
    fi
    exec nohup "$ROOT/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port 8002
  ) </dev/null >>"$DEV/analogue.log" 2>&1 &
  echo $! >"$DEV/analogue.pid"
  disown $! 2>/dev/null || true
  log "analogue: started pid=$(cat "$DEV/analogue.pid")"
}

start_inventory() {
  kill_port 8001
  (
    cd "$ROOT/services/inventory" || exit 1
    export PYTHONPATH="$ROOT/shared:$ROOT/services/inventory"
    export DATABASE_URL
    if [ -n "${JWT_PUBLIC_KEY:-}" ]; then
      export JWT_PUBLIC_KEY
    fi
    exec nohup "$ROOT/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port 8001
  ) </dev/null >>"$DEV/inventory.log" 2>&1 &
  echo $! >"$DEV/inventory.pid"
  disown $! 2>/dev/null || true
  log "inventory: started pid=$(cat "$DEV/inventory.pid")"
}

start_next() {
  kill_port 3000
  (
    cd "$ROOT/web" || exit 1
    exec nohup npm run dev -- --hostname 127.0.0.1 --port 3000
  ) </dev/null >>"$DEV/next.log" 2>&1 &
  echo $! >"$DEV/next.pid"
  disown $! 2>/dev/null || true
  log "next: started pid=$(cat "$DEV/next.pid")"
}

# Serialize overlapping stop hooks.
exec 9>"$LOCK"
if command -v flock >/dev/null 2>&1; then
  flock 9 || true
fi

ensure_postgres
load_runtime_env
ensure_web_token
python_import_check
web_tsc_check

start_analogue

INV_HEALTH="$(http_code "http://127.0.0.1:8001/healthz")"
if inventory_changed; then
  log "inventory: code changed; restarting"
  start_inventory
elif [ "$INV_HEALTH" != "200" ]; then
  log "inventory: not healthy ($INV_HEALTH); starting"
  start_inventory
else
  log "inventory: left running (healthz $INV_HEALTH)"
fi

start_next

wait_http "http://127.0.0.1:8002/healthz" 20 0.5 || true
wait_http "http://127.0.0.1:8001/healthz" 20 0.5 || true
wait_http "http://127.0.0.1:3000/analogue" 40 0.5 || true

log "hook done analogue=$(http_code http://127.0.0.1:8002/healthz) inventory=$(http_code http://127.0.0.1:8001/healthz) next=$(http_code http://127.0.0.1:3000/analogue)"
exit 0
