#!/usr/bin/env sh
# Build the two AppSail deploy folders: dist/api and dist/worker.
#
# AppSail (Catalyst-managed Python runtime) zips the build path as-is and does not run
# `pip install`, so dependencies are vendored into each folder. Wheels are fetched for the
# AppSail platform (Linux x86-64, CPython 3.12) so this works from macOS or Windows too.
#
#   ./scripts/build.sh            # both services
#   ./scripts/build.sh worker     # one service
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-python3}"
PYVER="${APPSAIL_PYTHON_VERSION:-3.12}"
SERVICES="${*:-api worker}"

for svc in $SERVICES; do
  dest="$ROOT/dist/$svc"
  rm -rf "$dest"
  mkdir -p "$dest"
  # shared code, prompts, config, template
  cp -R "$ROOT/core" "$ROOT/prompts" "$ROOT/config" "$ROOT/templates" "$dest/"
  mkdir -p "$dest/$svc"
  cp -R "$ROOT/$svc/." "$dest/$svc/"
  rm -f "$dest/$svc/app-config.json" "$dest/$svc/requirements.txt"
  # the API imports worker.steps (step manifest, section labels) and worker.context
  if [ "$svc" = "api" ]; then
    mkdir -p "$dest/worker/steps"
    cp "$ROOT/worker/__init__.py" "$ROOT/worker/context.py" "$dest/worker/"
    cp "$ROOT"/worker/steps/*.py "$dest/worker/steps/"
  fi
  # vendored dependencies for Linux x86-64 / CPython $PYVER
  sed "s#-r ../requirements-core.txt#-r $ROOT/requirements-core.txt#" "$ROOT/$svc/requirements.txt" > "$dest/.req.txt"
  "$PY" -m pip install --quiet --disable-pip-version-check \
      --target "$dest" --requirement "$dest/.req.txt" \
      --platform manylinux2014_x86_64 --platform manylinux_2_28_x86_64 \
      --implementation cp --python-version "$PYVER" --only-binary=:all: --upgrade
  rm -f "$dest/.req.txt"
  # trim what the runtime never needs
  find "$dest" -name "__pycache__" -type d -prune -exec rm -rf {} +
  find "$dest" -type d \( -name "tests" -o -name "test" \) -path "*/site-packages/*" -prune -exec rm -rf {} + 2>/dev/null || true
  rm -rf "$dest"/pyarrow/include "$dest"/numpy/_core/include "$dest"/*.dist-info/RECORD 2>/dev/null || true
  echo "built $dest ($(du -sh "$dest" | cut -f1))"
done
