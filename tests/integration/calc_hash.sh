#!/bin/bash
set -euo pipefail

DECODED_NAME=$1
INPUT_PATH=$2
OUTPUT_PATH=$3

if [[ -f "${INPUT_PATH}/${DECODED_NAME}.tbc" ]]; then
  sha256sum < "${INPUT_PATH}/${DECODED_NAME}.tbc" > "${OUTPUT_PATH}/${DECODED_NAME}.tbc.sha256"
fi

if [[ -f "${INPUT_PATH}/${DECODED_NAME}_chroma.tbc" ]]; then
  sha256sum < "${INPUT_PATH}/${DECODED_NAME}_chroma.tbc" > "${OUTPUT_PATH}/${DECODED_NAME}_chroma.tbc.sha256"
fi
