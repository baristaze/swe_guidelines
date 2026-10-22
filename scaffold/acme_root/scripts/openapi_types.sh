#!/usr/bin/env bash
# Generate each namespace's types from its OpenAPI document: one TypeScript
# file per document, and the Python schema package, one module per document,
# where a file `$ref` to another document is an import of its module. Each
# output folder is written afresh, so a namespace that is gone leaves no file.
# One openapi-typescript process writes every TypeScript file, from a Redocly
# config that lists each document as an API. Its empty `extends` lints with
# no rule, as the command does for one document it is given. `make openapi`
# runs it on the committed documents.
#
# Usage: scripts/openapi_types.sh <documents> <typescript-folder> <python-package>
set -euo pipefail
if [ $# -ne 3 ]; then
  echo "usage: $0 <documents> <typescript-folder> <python-package>" >&2
  exit 2
fi
documents=$(cd "$1" && pwd)
typescript=$2
python=$3

rm -rf "$typescript" "$python"
mkdir -p "$typescript"
typescript=$(cd "$typescript" && pwd)
config=$(mktemp -d)
trap 'rm -rf "$config"' EXIT
# A YAML single-quoted scalar, where a doubled quote is the one escape.
quote() { local q="'"; printf "'%s'" "${1//$q/$q$q}"; }
{
  echo "extends: []"
  echo "apis:"
  for document in "$documents"/*.json; do
    namespace=$(basename "$document" .json)
    echo "  $(quote "$namespace"):"
    echo "    root: $(quote "$document")"
    echo "    x-openapi-ts: {output: $(quote "$typescript/$namespace.d.ts")}"
  done
} > "$config/redocly.yaml"
pnpm --silent --filter @acme/client exec openapi-typescript --redocly "$config/redocly.yaml"
uv run datamodel-codegen --input "$documents" --input-file-type openapi --output "$python" \
  --output-model-type pydantic_v2.BaseModel --target-python-version 3.14 \
  --use-standard-collections --use-union-operator --use-annotated \
  --enum-field-as-literal none --deserialize-default-values enum \
  --use-schema-description --disable-timestamp \
  --formatters builtin
