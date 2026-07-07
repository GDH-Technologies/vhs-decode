#!/bin/bash
set -euo pipefail

DECODED_NAME=$1
INPUT_PATH=$2
OUTPUT_PATH=$3

check_file () {
  local FILE="$1"
  local HASH_FILE="$2"

  if [[ ! -f "$FILE" ]]; then
    echo "ERROR: Input file does not exist: $FILE" >&2
    exit 1
  fi

  if [[ ! -f "$HASH_FILE" ]]; then
    echo "ERROR: Hash file does not exist: $HASH_FILE" >&2
    exit 1
  fi

  ACTUAL_HASH=$(sha256sum < "$FILE" | awk '{print $1}')
  EXPECTED_HASH=$(awk '{print $1}' "$HASH_FILE")

  if [[ "$ACTUAL_HASH" != "$EXPECTED_HASH" ]]; then
    echo "ERROR: Hash mismatch for $FILE" >&2
    echo "Expected: $EXPECTED_HASH" >&2
    echo "Actual:   $ACTUAL_HASH" >&2
    exit 2
  fi

  echo "OK: Verified $FILE"
}

LUMA_FILE="${INPUT_PATH}/${DECODED_NAME}.tbc"
LUMA_HASH="${OUTPUT_PATH}/${DECODED_NAME}.tbc.sha256"

if [[ -f "$LUMA_HASH" ]]; then
  check_file "$LUMA_FILE" "$LUMA_HASH"
fi

CHROMA_FILE="${INPUT_PATH}/${DECODED_NAME}_chroma.tbc"
CHROMA_HASH="${OUTPUT_PATH}/${DECODED_NAME}_chroma.tbc.sha256"

if [[ -f "$CHROMA_HASH" ]]; then
  check_file "$CHROMA_FILE" "$CHROMA_HASH"
fi
