"""Each namespace's OpenAPI document: the app's document, cut by namespace.

A route belongs to the namespace its tag names, and a schema to the
namespace whose module defines it: a wire type's `types/<namespace>.py`, or
an enum of the object model's `om/<namespace>/`. What no namespace defines
is `common`'s: the bases and the error envelope of `types/common.py`, the
context's roles, and the framework's validation error. Each document holds
its namespace's paths and schemas under the names their classes carry, and
refers to another namespace's schema by a file `$ref` to that namespace's
document. So each schema is defined once, a name need not be unique across
namespaces, and a change to one namespace rewrites its own document alone."""

import json
from collections.abc import Iterator
from enum import Enum
from pathlib import Path
from types import UnionType
from typing import Any, get_args, get_origin

from fastapi import FastAPI
from fastapi.openapi.utils import (
    get_fields_from_routes,
    validation_error_definition,
    validation_error_response_definition,
)
from pydantic import BaseModel

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
    document. The framework names a schema by its class, or by its module and
    its class where two classes share a name; the document takes the class's
    name, since the file names the namespace."""
    models = set(_models(app))
    by_framework_name: dict[str, type] = {}
    for model in models:
        qualified = f"{model.__module__}.{model.__qualname__}".replace(".", "__")
        candidate = model.__name__ if model.__name__ in names else qualified
        if by_framework_name.setdefault(candidate, model) is not model:
            raise ValueError(f"two types a route names are both {candidate}")
    owner: dict[str, str] = {}
    name: dict[str, str] = {}
    for schema in names:
        if schema in FRAMEWORK:
            owner[schema], name[schema] = COMMON, schema
            continue
        model = by_framework_name.get(schema)
        if model is None:
            raise ValueError(f"no route's type is named {schema}; its namespace is unknown")
        owner[schema], name[schema] = namespace_of(model), model.__name__
    return owner, name


def _models(app: FastAPI) -> Iterator[type]:
    """Every model and enum the app's schema can name: each route's body,
    parameters, answer, and declared responses, as the framework collects
    them, and every model and enum they hold."""
    stack: list[Any] = [field.field_info.annotation for field in get_fields_from_routes(app.routes)]
    seen: set[int] = set()
    while stack:
        hint = stack.pop()
        if hint is None or id(hint) in seen:
            continue
        seen.add(id(hint))
        if isinstance(hint, type) and issubclass(hint, Enum):
            yield hint
        elif isinstance(hint, type) and issubclass(hint, BaseModel):
            yield hint
            stack.extend(field.annotation for field in hint.model_fields.values())
        elif get_origin(hint) is not None or isinstance(hint, UnionType):
            stack.extend(get_args(hint))


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
