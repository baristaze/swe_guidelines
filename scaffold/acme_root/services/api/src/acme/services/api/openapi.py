"""Each namespace's OpenAPI document: the app's document, cut by namespace.

A route belongs to the namespace its tag names, and a schema to the
namespace whose module defines it: a wire type's `types/<namespace>.py`, or
an enum of the object model's `om/<namespace>/`. What no namespace defines
is `common`'s: the bases and the error envelope of `types/common.py`, the
context's roles, and the framework's validation error. Each document holds
its namespace's paths and schemas under the names the framework gives their
types, less any module in them, and refers to another namespace's schema by
a file `$ref` to that namespace's document. So each schema is defined once, a name need not be unique across
namespaces, and a change to one namespace rewrites its own document alone."""

import json
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import (
    get_fields_from_routes,
    validation_error_definition,
    validation_error_response_definition,
)
from pydantic import TypeAdapter
from pydantic.json_schema import GenerateJsonSchema

import acme.om
import acme.services.api.types
from acme.services.api.routers import HOSTED

COMMON = "common"
"""The document of what no namespace defines, which every namespace may use."""

SCHEMAS = "#/components/schemas/"
TYPES = acme.services.api.types.__name__
OM = acme.om.__name__
FRAMEWORK = frozenset(
    {validation_error_definition["title"], validation_error_response_definition["title"]}
)
"""The schemas the framework adds for its own validation error, with no class
of ours behind them."""
MODES = {"": None, "-Input": "validation", "-Output": "serialization"}
"""The suffix pydantic gives a type's schema where the schema it reads and
the one it writes differ, and the mode each names."""
WRITTEN = frozenset({"serialization", "computed_fields"})
"""The parts of a core schema that only a written value takes."""
_NAMING = GenerateJsonSchema()


def namespace_of(model: type) -> str:
    """The namespace whose module defines a wire type or an enum."""
    module = model.__module__
    if module.startswith(f"{TYPES}."):
        name = module.removeprefix(f"{TYPES}.").split(".")[0]
        if name != COMMON and name not in HOSTED:
            raise ValueError(
                f"{model.__name__} is defined in {module}, and {name} is no hosted namespace: "
                "a wire type lives in types/<namespace>.py or types/common.py"
            )
        return name
    if module.startswith(f"{OM}."):
        parts = module.removeprefix(f"{OM}.").split(".")
        # A module of the object model's own, such as its context, is no namespace's.
        return parts[0] if len(parts) > 1 else COMMON
    raise ValueError(
        f"{model.__name__} is defined in {module}: a wire type lives in types/<namespace>.py"
    )


def documents(app: FastAPI) -> dict[str, dict[str, Any]]:
    """The app's document, as one document per namespace and one for `common`."""
    whole = app.openapi()
    owner, name = _schema_owners(app, set(whole.get("components", {}).get("schemas", {})))
    extra = set(whole.get("components", {})) - {"schemas"}
    if extra:
        raise ValueError(
            f"the document holds components of kinds {sorted(extra)}; only schemas are cut"
        )
    descriptions = {tag["name"]: tag for tag in whole.get("tags", [])}
    namespaces = sorted({COMMON, *HOSTED, *owner.values()})
    out: dict[str, dict[str, Any]] = {}
    for namespace in namespaces:
        out[namespace] = {
            "openapi": whole["openapi"],
            "info": {**whole["info"], "title": f"{whole['info']['title']}: {namespace}"},
            "paths": {},
            "components": {"schemas": {}},
        }
        if namespace in descriptions:
            out[namespace]["tags"] = [descriptions[namespace]]
    seen: dict[tuple[str, str], str] = {}
    for path, item in whole.get("paths", {}).items():
        for method, operation in item.items():
            namespace = _namespace_of_operation(method, path, operation)
            operation_id = operation.get("operationId", "")
            if (namespace, operation_id) in seen:
                raise ValueError(
                    f"{method.upper()} {path} and {seen[namespace, operation_id]} share the "
                    f"operation id {operation_id} in {namespace}'s document"
                )
            seen[namespace, operation_id] = f"{method.upper()} {path}"
            paths = out[namespace]["paths"]
            paths.setdefault(path, {})[method] = _refer(operation, namespace, owner, name)
    for schema, definition in whole.get("components", {}).get("schemas", {}).items():
        namespace = owner[schema]
        defined = out[namespace]["components"]["schemas"]
        if name[schema] in defined:
            raise ValueError(f"two schemas of {namespace} are named {name[schema]}")
        defined[name[schema]] = _refer(definition, namespace, owner, name)
    return out


def write(documents: dict[str, dict[str, Any]], folder: Path) -> list[Path]:
    """Writes each document to `<folder>/<namespace>.json` and removes a
    document no namespace emits any more."""
    folder.mkdir(parents=True, exist_ok=True)
    for stale in folder.glob("*.json"):
        if stale.stem not in documents:
            stale.unlink()
    written = []
    for namespace, document in documents.items():
        path = folder / f"{namespace}.json"
        path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
        written.append(path)
    return written


def _namespace_of_operation(method: str, path: str, operation: dict[str, Any]) -> str:
    tags = operation.get("tags", [])
    if len(tags) != 1 or tags[0] not in HOSTED:
        raise ValueError(
            f"{method.upper()} {path} is tagged {tags}; a route's one tag is its hosted namespace"
        )
    return tags[0]


def _schema_owners(app: FastAPI, names: set[str]) -> tuple[dict[str, str], dict[str, str]]:
    """Each schema's namespace, and the name it takes in that namespace's
    document. Pydantic names a schema after its type's core ref: by the type's
    name, with its mode where the schema it reads and the one it writes
    differ, and by its module and qualified name, with an occurrence index,
    where two types would share a name. The document takes the short form,
    since the file names the namespace."""
    types = [(model, modes, *_framework_names(ref)) for ref, (model, modes) in _types(app).items()]
    owner: dict[str, str] = {}
    name: dict[str, str] = {}
    for schema in names:
        if schema in FRAMEWORK:
            owner[schema], name[schema] = COMMON, schema
            continue
        matches = {
            model: local
            for model, modes, short, qualified in types
            if (local := _local_name(schema, modes, short, qualified)) is not None
        }
        if not matches:
            raise ValueError(f"no route's type is named {schema}; its namespace is unknown")
        if len(matches) > 1:
            raise ValueError(f"two types a route names are both {schema}")
        ((model, local),) = matches.items()
        owner[schema], name[schema] = namespace_of(model), local
    return owner, name


def _types(app: FastAPI) -> dict[str, tuple[type, set[str]]]:
    """Every model and enum the app's schema can name, by its core ref, and
    the modes it is read or written in: each one the core schema of a route's
    body, parameters, answer, or declared responses holds, as the framework
    collects them, a computed field's and a generic's included."""
    roots = [
        (TypeAdapter(field.field_info.annotation).core_schema, field.mode)
        for field in get_fields_from_routes(app.routes)
    ]
    found: dict[str, tuple[type, set[str]]] = {}
    stack: list[tuple[Any, str]] = list(roots)
    seen: set[tuple[int, str]] = set()
    while stack:
        node, mode = stack.pop()
        if (id(node), mode) in seen:
            continue
        seen.add((id(node), mode))
        if isinstance(node, dict):
            if isinstance(node.get("ref"), str) and isinstance(node.get("cls"), type):
                found.setdefault(node["ref"], (node["cls"], set()))[1].add(mode)
            node = [v for k, v in node.items() if mode == "serialization" or k not in WRITTEN]
        stack.extend((value, mode) for value in node if isinstance(value, dict | list | tuple))
    return found


def _framework_names(ref: str) -> tuple[str, str]:
    """The short name and the module-qualified name pydantic derives from a
    core ref (`GenerateJsonSchema.get_defs_ref`); a generic's names hold its
    parameters'."""
    components = [part.rsplit(":", 1)[0] for part in re.split(r"([\][,])", ref)]
    short = "".join(re.sub(r"(?:[^.[\]]+\.)+((?:[^.[\]]+))", r"\1", part) for part in components)
    return _NAMING.normalize_name(short), _NAMING.normalize_name("".join(components))


def _local_name(schema: str, modes: set[str], short: str, qualified: str) -> str | None:
    """The name `schema` takes in its namespace's document when pydantic gave
    it to the type of these names, read or written in `modes`, else None.
    Pydantic's choices are the short name and the qualified one, each bare or
    with a mode the type takes, and the qualified one with an occurrence index."""
    for suffix, mode in MODES.items():
        if mode is not None and mode not in modes:
            continue
        if schema == f"{short}{suffix}":
            return schema
        if re.fullmatch(rf"{re.escape(qualified + suffix)}(__\d+)?", schema):
            return f"{short}{suffix}"
    return None


def _refer(node: Any, here: str, owner: dict[str, str], name: dict[str, str]) -> Any:
    """`node` with each schema `$ref` pointing at its owner's document."""
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and value.startswith(SCHEMAS):
                schema = value.removeprefix(SCHEMAS)
                there = owner[schema]
                prefix = "" if there == here else f"{there}.json"
                out[key] = f"{prefix}{SCHEMAS}{name[schema]}"
            else:
                out[key] = _refer(value, here, owner, name)
        return out
    if isinstance(node, list):
        return [_refer(value, here, owner, name) for value in node]
    return node
