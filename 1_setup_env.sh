#!/usr/bin/env bash
set -euo pipefail

OLLAMA_CONTAINER_NAME="${OLLAMA_CONTAINER_NAME:-ephemeral-ollama}"
OLLAMA_IMAGE="${OLLAMA_IMAGE:-ollama/ollama}"
OLLAMA_WORKSPACE="${OLLAMA_WORKSPACE:-/$HOME/finveri_ollama_workspace}"

POSTGEN_MODEL="${POSTGEN_MODEL:-llama3.1:8b}"
BASELINE_MODEL="${BASELINE_MODEL:-qwen2.5-coder:7b-instruct}"
OLLAMA_HOST_URL="${OLLAMA_HOST_URL:-http://localhost:11434}"
OLLAMA_READY_TIMEOUT_SECONDS="${OLLAMA_READY_TIMEOUT_SECONDS:-120}"
OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-4}"

require_command() {
  local name="$1"
  if ! command -v "$name" >/dev/null 2>&1; then
    echo "Required command not found: $name" >&2
    exit 1
  fi
}

container_exists() {
  docker ps -a --format '{{.Names}}' | grep -Fxq "$OLLAMA_CONTAINER_NAME"
}

wait_for_ollama() {
  local elapsed=0

  echo "Waiting for Ollama server at ${OLLAMA_HOST_URL}..."
  until curl -fsS "${OLLAMA_HOST_URL}/api/tags" >/dev/null 2>&1; do
    if (( elapsed >= OLLAMA_READY_TIMEOUT_SECONDS )); then
      echo "Ollama did not become ready within ${OLLAMA_READY_TIMEOUT_SECONDS}s." >&2
      echo "Recent container logs:" >&2
      docker logs --tail 80 "$OLLAMA_CONTAINER_NAME" >&2 || true
      exit 1
    fi

    sleep 2
    elapsed=$((elapsed + 2))
  done
}

require_command docker
require_command curl

if ! docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi >/dev/null 2>&1; then
  echo "Docker GPU access is unavailable. Install/configure the NVIDIA Container Toolkit, then verify 'docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi'." >&2
  exit 1
fi

if container_exists; then
  echo "Container '${OLLAMA_CONTAINER_NAME}' already exists." >&2
  echo "Run ./3_clean_up.sh first, then retry setup." >&2
  exit 1
fi

mkdir -p "$OLLAMA_WORKSPACE"

echo "Starting Ollama Docker container..."
echo "Container : ${OLLAMA_CONTAINER_NAME}"
echo "Image     : ${OLLAMA_IMAGE}"
echo "Workspace : ${OLLAMA_WORKSPACE}"
echo "Parallel requests : ${OLLAMA_NUM_PARALLEL}"

docker run -d \
  --name "$OLLAMA_CONTAINER_NAME" \
  --rm \
  --gpus all \
  -p 11434:11434 \
  -e "OLLAMA_NUM_PARALLEL=${OLLAMA_NUM_PARALLEL}" \
  -v "${OLLAMA_WORKSPACE}:/root/.ollama" \
  "$OLLAMA_IMAGE"

wait_for_ollama

echo "Pulling post-generation model: ${POSTGEN_MODEL}"
docker exec "$OLLAMA_CONTAINER_NAME" ollama pull "$POSTGEN_MODEL"

echo "Pulling baseline SQL generator model: ${BASELINE_MODEL}"
docker exec "$OLLAMA_CONTAINER_NAME" ollama pull "$BASELINE_MODEL"

echo
echo "Available Ollama models:"
docker exec "$OLLAMA_CONTAINER_NAME" ollama list

echo
echo "Environment is ready. You can now run ./2_run_ablations.sh"
