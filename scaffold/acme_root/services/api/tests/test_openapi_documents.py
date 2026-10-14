"""Each namespace's OpenAPI document, held over what `make openapi` emits: a
schema is defined once, in the document of the namespace whose module
defines it, and every other use is a file `$ref` to it; a route is in one
document; and a change to one namespace rewrites its own files alone."""

import filecmp
import importlib
import pkgutil
import subprocess
import sys
from decimal import Decimal
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any, Generic, TypeVar

import pytest
from api_support import build_container
from fastapi import APIRouter, FastAPI
from fastapi.openapi.utils import validation_error_definition, validation_error_response_definition
from pydantic import BaseModel, ModelWrapValidatorHandler, computed_field, model_validator

import acme.om
import acme.services.api.types
from acme.services.api.app import create_app
from acme.services.api.openapi import COMMON, documents, write
from acme.services.api.routers import HOSTED
from acme.services.api.types.common import RequestBody, View

ROOT = Path(__file__).resolve().parents[3]
T = TypeVar("T")
SCHEMAS = "#/components/schemas/"
FRAMEWORK = {validation_error_definition["title"], validation_error_response_definition["title"]}


def defining_namespaces() -> dict[str, set[str]]:
    """Each wire type's and enum's name, and the namespaces whose modules
    define a class of that name: `types/<namespace>.py`, `om/<namespace>/`,
    and the object model's own modules for `common`."""
    found: dict[str, set[str]] = {}
    for package, own in ((acme.services.api.types, None), (acme.om, COMMON)):
        prefix = f"{package.__name__}."
        for info in pkgutil.walk_packages(package.__path__, prefix):
            module = importlib.import_module(info.name)
            parts = info.name.removeprefix(prefix).split(".")
            namespace = parts[0] if (own is None or len(parts) > 1) else own
            for value in vars(module).values():
                if (
                    isinstance(value, type)
                    and issubclass(value, BaseModel | Enum)
                    and value.__module__ == info.name
                ):
                    found.setdefault(value.__name__, set()).add(namespace)
    return found


def refs(node: Any) -> list[str]:
    if isinstance(node, dict):
        return [
            ref
            for key, value in node.items()
            for ref in ([value] if key == "$ref" else refs(value))
        ]
    if isinstance(node, list):
        return [ref for value in node for ref in refs(value)]
    return []


@pytest.fixture
def emitted(tmp_path: Path) -> dict[str, dict[str, Any]]:
    return documents(create_app(build_container(tmp_path)))


def test_each_schema_is_defined_once_in_the_document_of_its_defining_module(
    emitted: dict[str, dict[str, Any]],
) -> None:
    owners = defining_namespaces()
    assert set(emitted) == {COMMON, *HOSTED}
    for namespace, document in emitted.items():
        for name in document["components"]["schemas"]:
            if name in FRAMEWORK:
                assert namespace == COMMON, f"{name} is the framework's, so common's"
            else:
                assert namespace in owners.get(name, set()), (
                    f"{namespace}.json defines {name}, which no module of {namespace} defines"
                )
    defined = [
        (name, namespace)
        for namespace, document in emitted.items()
        for name in document["components"]["schemas"]
    ]
    for name, namespace in defined:
        others = [n for other, n in defined if other == name and n != namespace]
        assert not others or owners[name] >= {namespace, *others}, (
            f"{name} is defined in {namespace}.json and {others}"
        )


def test_every_use_of_another_namespaces_schema_is_a_file_ref_to_its_document(
    emitted: dict[str, dict[str, Any]],
) -> None:
    for namespace, document in emitted.items():
        for ref in refs(document):
            there, _, pointer = ref.partition("#")
            assert pointer.startswith("/components/schemas/"), ref
            name = pointer.removeprefix("/components/schemas/")
            if there:
                target = there.removesuffix(".json")
                assert target != namespace, f"{namespace}.json refers to itself by file: {ref}"
                assert name in emitted[target]["components"]["schemas"], ref
            else:
                assert name in document["components"]["schemas"], (
                    f"{namespace}.json refers to {name} as its own, and does not define it"
                )


def test_no_route_is_in_two_documents_and_none_is_lost(
    emitted: dict[str, dict[str, Any]], tmp_path: Path
) -> None:
    pairs = [
        (method, path)
        for document in emitted.values()
        for path, item in document["paths"].items()
        for method in item
    ]
    assert len(pairs) == len(set(pairs))
    whole = create_app(build_container(tmp_path)).openapi()
    assert set(pairs) == {(m, p) for p, item in whole["paths"].items() for m in item}
    for namespace, document in emitted.items():
        operations = [op for item in document["paths"].values() for op in item.values()]
        assert all(op["tags"] == [namespace] for op in operations)
        ids = [op["operationId"] for op in operations]
        assert len(ids) == len(set(ids)), f"{namespace}.json repeats an operation id"
        if namespace != COMMON:
            (tag,) = document["tags"]
            assert tag["name"] == namespace and tag["description"]


def app_of(*routers: APIRouter) -> FastAPI:
    app = FastAPI()
    for router in routers:
        app.include_router(router)
    return app


def view(name: str, namespace: str, **fields: Any) -> type[View]:
    """A wire type named `name`, defined as `types/<namespace>.py` would."""
    module = f"{acme.services.api.types.__name__}.{namespace}"
    return type(name, (View,), {"__module__": module, "__annotations__": fields})


def test_two_namespaces_may_each_name_a_type_alike() -> None:
    media_probe = view("Probe", "media", size=int)
    leases_probe = view("Probe", "leases", label=str)
    holder = view("ProbeHolder", "leases", own=leases_probe, other=media_probe)
    media, leases = APIRouter(tags=["media"]), APIRouter(tags=["leases"])
    media.add_api_route("/media/probe", lambda: None, response_model=media_probe)
    leases.add_api_route("/leases/probe", lambda: None, response_model=holder)

    emitted = documents(app_of(media, leases))

    assert emitted["media"]["components"]["schemas"]["Probe"]["properties"].keys() == {"size"}
    assert emitted["leases"]["components"]["schemas"]["Probe"]["properties"].keys() == {"label"}
    held = emitted["leases"]["components"]["schemas"]["ProbeHolder"]["properties"]
    assert held["own"] == {"$ref": "#/components/schemas/Probe"}
    assert held["other"] == {"$ref": "media.json#/components/schemas/Probe"}


def test_a_type_read_and_written_differently_has_a_schema_per_mode_in_its_document() -> None:
    """A decimal is read as a number or a string and written as a string, so
    pydantic names its holder's two schemas by mode, and by module as well
    where another namespace names a type alike."""
    media_money = view("Money", "media", amount=Decimal)
    leases_money = view("Money", "leases", amount=Decimal, currency=str)
    request = view("PriceRequest", "media", price=media_money)
    answer = view("PriceView", "leases", own=leases_money, other=media_money)
    media, leases = APIRouter(tags=["media"]), APIRouter(tags=["leases"])

    def price(body: request) -> None:  # pyright: ignore[reportInvalidTypeForm] (a type built at run time)
        return None

    media.add_api_route("/media/price", price, methods=["POST"])
    leases.add_api_route("/leases/price", lambda: None, response_model=answer)

    emitted = documents(app_of(media, leases))

    media_schemas = emitted["media"]["components"]["schemas"]
    leases_schemas = emitted["leases"]["components"]["schemas"]
    assert {"Money-Input", "Money-Output"} <= media_schemas.keys()
    assert media_schemas["PriceRequest"]["properties"]["price"] == {
        "$ref": "#/components/schemas/Money-Input"
    }
    assert leases_schemas["Money"]["properties"].keys() == {"amount", "currency"}
    held = leases_schemas["PriceView"]["properties"]
    assert held["own"] == {"$ref": "#/components/schemas/Money"}
    assert held["other"] == {"$ref": "media.json#/components/schemas/Money-Output"}


def test_a_generic_type_is_its_modules_under_the_name_its_parameters_give_it() -> None:
    item = view("ItemView", "media", n=int)

    class Page(View, Generic[T]):
        __module__ = f"{acme.services.api.types.__name__}.media"
        items: list[T]

    router = APIRouter(tags=["media"])
    router.add_api_route("/media/items", lambda: None, response_model=Page[item])

    schemas = documents(app_of(router))["media"]["components"]["schemas"]

    assert schemas.keys() == {"ItemView", "Page_ItemView_"}
    assert schemas["Page_ItemView_"]["properties"]["items"]["items"] == {
        "$ref": "#/components/schemas/ItemView"
    }


def test_an_enum_only_a_computed_field_returns_is_its_modules() -> None:
    class Shade(StrEnum):
        __module__ = f"{acme.om.__name__}.leases.probe"
        DARK = "dark"

    class TintView(View):
        __module__ = f"{acme.services.api.types.__name__}.media"
        n: int

        @computed_field
        @property
        def shade(self) -> Shade:
            return Shade.DARK

    router = APIRouter(tags=["media"])
    router.add_api_route("/media/tint", lambda: None, response_model=TintView)

    emitted = documents(app_of(router))

    assert emitted["leases"]["components"]["schemas"]["Shade"]["enum"] == ["dark"]
    shade = emitted["media"]["components"]["schemas"]["TintView"]["properties"]["shade"]
    assert shade["$ref"] == "leases.json#/components/schemas/Shade"


def test_a_type_a_model_validator_wraps_is_its_modules() -> None:
    """Pydantic puts the ref of a model a model validator wraps on the
    validator's node, and the model's class beneath it: a request so wrapped,
    and a type it holds, are each their module's."""

    class Window(RequestBody):
        __module__ = f"{acme.services.api.types.__name__}.leases"
        start: int
        end: int

        @model_validator(mode="wrap")
        @classmethod
        def ordered(cls, data: Any, handler: ModelWrapValidatorHandler[Window]) -> Window:
            return handler(data)

    class BookRequest(RequestBody):
        __module__ = f"{acme.services.api.types.__name__}.media"
        window: Window

        @model_validator(mode="after")
        def checked(self) -> BookRequest:
            return self

    def book(body: BookRequest) -> None:
        return None

    router = APIRouter(tags=["media"])
    router.add_api_route("/media/book", book, methods=["POST"])

    emitted = documents(app_of(router))

    request = emitted["media"]["components"]["schemas"]["BookRequest"]
    assert request["properties"]["window"] == {"$ref": "leases.json#/components/schemas/Window"}
    assert emitted["leases"]["components"]["schemas"]["Window"]["properties"].keys() == {
        "start",
        "end",
    }


def test_an_enum_default_of_a_request_field_is_its_member_in_the_python_client(
    tmp_path: Path,
) -> None:
    """The document holds an enum field's default as its value; the generated
    client sets it to the enum's member, which its declared type takes, for an
    enum of the request's namespace and one of another's."""

    class Tone(StrEnum):
        __module__ = f"{acme.om.__name__}.media.probe"
        LOUD = "loud"
        SOFT = "soft"

    class Shade(StrEnum):
        __module__ = f"{acme.om.__name__}.leases.probe"
        DARK = "dark"
        LIGHT = "light"

    class ToneRequest(RequestBody):
        __module__ = f"{acme.services.api.types.__name__}.media"
        tone: Tone = Tone.SOFT
        shade: Shade | None = Shade.LIGHT

    def tone(body: ToneRequest) -> None:
        return None

    router = APIRouter(tags=["media"])
    router.add_api_route("/media/tone", tone, methods=["POST"])
    write(documents(app_of(router)), tmp_path / "openapi")
    subprocess.run(
        [ROOT / "scripts/openapi_types.sh", tmp_path / "openapi", tmp_path / "ts", tmp_path / "py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )

    typed = subprocess.run(
        [sys.executable, "-m", "pyright", tmp_path / "py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert typed.returncode == 0, typed.stdout


def test_a_route_whose_tag_is_no_hosted_namespace_is_refused() -> None:
    router = APIRouter(tags=["admin"])
    router.add_api_route("/admin/probe", lambda: None, response_model=view("Probe", "media"))
    with pytest.raises(ValueError, match="one tag is its hosted namespace"):
        documents(app_of(router))


def test_a_wire_type_outside_a_namespace_module_is_refused() -> None:
    router = APIRouter(tags=["media"])
    router.add_api_route("/media/probe", lambda: None, response_model=view("Probe", "admin"))
    with pytest.raises(ValueError, match="no hosted namespace"):
        documents(app_of(router))


def test_a_route_and_a_type_of_one_namespace_change_its_files_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`make openapi` over the app, then over the app with one more route and
    type in media: what differs is media's document and its two generated
    files, and nothing of another namespace."""

    def make_openapi(into: Path) -> None:
        (tmp_path / into.name).mkdir(parents=True, exist_ok=True)
        write(documents(create_app(build_container(tmp_path / into.name))), into / "openapi")
        subprocess.run(
            [ROOT / "scripts/openapi_types.sh", into / "openapi", into / "ts", into / "py"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )

    make_openapi(tmp_path / "before")
    probe = APIRouter(prefix="/media", tags=["media"])
    probe.add_api_route("/probe", lambda: None, response_model=view("ProbeView", "media", n=int))
    monkeypatch.setitem(HOSTED, "media", (*HOSTED["media"], probe))
    make_openapi(tmp_path / "after")

    def changed(compare: filecmp.dircmp[str], at: str = "") -> set[str]:
        out = {
            f"{at}{name}" for name in compare.diff_files + compare.left_only + compare.right_only
        }
        for name, sub in compare.subdirs.items():
            out |= changed(sub, f"{at}{name}/")
        return out

    difference = changed(filecmp.dircmp(tmp_path / "before", tmp_path / "after"))
    assert difference == {"openapi/media.json", "ts/media.d.ts", "py/media.py"}
