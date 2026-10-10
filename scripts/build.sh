#!/usr/bin/env sh
# Copy the shared folders into each service's deploy folder before `catalyst deploy`.
# (Confirm with Catalyst whether AppSail can deploy from the parent directory instead.)
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
for svc in api worker; do
  dest="$ROOT/dist/$svc"
  rm -rf "$dest"
  mkdir -p "$dest"
  cp -R "$ROOT/core" "$ROOT/prompts" "$ROOT/config" "$ROOT/templates" "$dest/"
  cp "$ROOT/requirements-core.txt" "$dest/"
  mkdir -p "$dest/$svc"
  cp -R "$ROOT/$svc/." "$dest/$svc/"
  # the API imports worker.steps (step manifest, section labels) and worker.context
  if [ "$svc" = "api" ]; then
    mkdir -p "$dest/worker/steps"
    cp "$ROOT/worker/__init__.py" "$ROOT/worker/context.py" "$dest/worker/"
    cp "$ROOT"/worker/steps/*.py "$dest/worker/steps/"
  fi
  sed 's#-r ../requirements-core.txt#-r requirements-core.txt#' "$ROOT/$svc/requirements.txt" > "$dest/requirements.txt"
  cp "$ROOT/$svc/app-config.json" "$dest/app-config.json"
  find "$dest" -name "__pycache__" -type d -prune -exec rm -rf {} +
  echo "built $dest"
done
