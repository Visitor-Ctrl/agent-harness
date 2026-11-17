#!/bin/sh
set -eu
harness_source=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 -I -c 'import sys; sys.path.insert(0, sys.argv.pop(1) + "/lib"); from agent_harness.admin import main; raise SystemExit(main(installer=True))' "$harness_source" "$@"
