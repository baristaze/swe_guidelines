"""The client is each namespace's calls composed onto one transport, one
module per namespace, named as its document is. No two modules define the
same operation, so composing them hides none."""

from pathlib import Path

from acme.client import calls
from acme.client.client import ApiClient
from acme.client.transport import Transport

DOCUMENTS = Path(__file__).resolve().parents[2] / "openapi"


def test_each_namespaces_calls_are_a_module_named_as_its_document() -> None:
    parts = ApiClient.__bases__
    assert all(issubclass(part, Transport) for part in parts)
    modules = {part.__module__ for part in parts}
    assert all(module.startswith(f"{calls.__name__}.") for module in modules)
    namespaces = {module.removeprefix(f"{calls.__name__}.") for module in modules}
    assert len(namespaces) == len(parts), "one class of calls per namespace module"
    assert namespaces <= {document.stem for document in DOCUMENTS.glob("*.json")}


def test_no_two_namespaces_define_the_same_operation() -> None:
    operations = [
        name for part in ApiClient.__bases__ for name in vars(part) if not name.startswith("__")
    ]
    assert len(operations) == len(set(operations))
