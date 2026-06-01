#!/bin/bash

VHS_DECODE_ROOT="${GITHUB_WORKSPACE:-$(pwd)}"

python3 -m pytest --rootdir="$VHS_DECODE_ROOT" "$VHS_DECODE_ROOT/tests/unit" -v
