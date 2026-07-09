#!/usr/bin/env bash
set -euo pipefail

OLLAMA_CONTAINER_NAME="${OLLAMA_CONTAINER_NAME:-ephemeral-ollama}"
OLLAMA_IMAGE="${OLLAMA_IMAGE:-ollama/ollama}"
OLLAMA_WORKSPACE="${OLLAMA_WORKSPACE:-/$HOME/finveri_ollama_workspace}"

require_command() {
  local name="$1"
  if ! command -v "$name" >/dev/null 2>&1; then
    echo "Required command not found: $name" >&2
    exit 1
  fi
}

safe_delete_workspace() {
  local path="$1"

  if [[ -z "$path" ]]; then
    echo "Refusing to delete empty workspace path." >&2
    exit 1
  fi

  case "$path" in
    "/"|"/tmp"|"/tmp/"|"/tmp_deacon"|"/tmp_deacon/")
      echo "Refusing to delete unsafe workspace path: $path" >&2
      exit 1
      ;;
  esac

  if [[ "$path" != *"finveri_ollama_workspace"* ]]; then
    echo "Refusing to delete path that does not look like the Ollama workspace: $path" >&2
    exit 1
  fi

  rm -rf -- "$path"
}

require_command docker

echo "Stopping Ollama container if present: ${OLLAMA_CONTAINER_NAME}"
docker stop "$OLLAMA_CONTAINER_NAME" >/dev/null 2>&1 || true

echo "Deleting Ollama model workspace: ${OLLAMA_WORKSPACE}"
safe_delete_workspace "$OLLAMA_WORKSPACE"

echo "Removing Docker image if present: ${OLLAMA_IMAGE}"
docker image rm "$OLLAMA_IMAGE" >/dev/null 2>&1 || true

echo
echo "Cleanup complete. Container is stopped/removed, ${OLLAMA_WORKSPACE} is deleted, and ${OLLAMA_IMAGE} has been removed if it was present."
