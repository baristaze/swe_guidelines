"""Every interface is abstract, checked rather than written down: a class
named `*Interface` under the object model or the infrastructure toolkit is an
`ABC`, and every public method it declares is abstract, so an impl that
forgets one is refused when it is built, not when the method is first
called and returns None. And every read that returns a list is bounded: a
storage interface method or a bucket listing that returns a list takes a
`limit`, which the caller picks; that both impls honor it, in the statement
and at the same row, is each namespace's contract suite."""

import importlib
import inspect
import pkgutil
from abc import ABC
from annotationlib import Format, get_annotations
from collections.abc import Callable, Iterator
from types import ModuleType

import pytest

import acme.infra
import acme.om
from acme.infra.buckets import BucketsInterface


def interfaces(package: ModuleType) -> Iterator[type]:
    for module_info in pkgutil.walk_packages(package.__path__, prefix=f"{package.__name__}."):
        module = importlib.import_module(module_info.name)
        for name, obj in vars(module).items():
            if (
                inspect.isclass(obj)
                and name.endswith("Interface")
                and obj.__module__ == module.__name__
            ):
                yield obj


INTERFACES = sorted(
    {*interfaces(acme.om), *interfaces(acme.infra)}, key=lambda cls: cls.__qualname__
)


def public_methods(interface: type) -> list[str]:
    return [
        name
        for name, member in vars(interface).items()
        if not name.startswith("_") and inspect.isfunction(member)
    ]


def test_the_scan_finds_the_roots() -> None:
    names = {cls.__name__ for cls in INTERFACES}
    assert {"StorageInterface", "InfraInterface", "TenancyManagerInterface"} <= names


@pytest.mark.parametrize("interface", INTERFACES, ids=lambda cls: cls.__name__)
def test_every_interface_is_an_abc_with_abstract_methods(interface: type) -> None:
    assert issubclass(interface, ABC), f"{interface.__name__} is not an ABC"
    methods = public_methods(interface)
    assert methods, f"{interface.__name__} declares no method"
    concrete = [
        name
        for name in methods
        if not getattr(vars(interface)[name], "__isabstractmethod__", False)
    ]
    assert not concrete, f"{interface.__name__} has concrete methods: {concrete}"


BOUNDED = [
    cls
    for cls in INTERFACES
    if cls.__name__.endswith("StorageInterface") or cls is BucketsInterface
]


def signature(method: Callable[..., object]) -> inspect.Signature:
    return inspect.signature(method, annotation_format=Format.STRING)


def list_reads(interface: type) -> list[str]:
    """The methods annotated to return a list. Read as strings: in a class
    that declares a method named `list`, the name no longer means the type."""
    return [
        name
        for name in public_methods(interface)
        if get_annotations(getattr(interface, name), format=Format.STRING)
        .get("return", "")
        .startswith("list[")
    ]


def test_the_scan_finds_the_list_reads() -> None:
    found = {(cls.__name__, name) for cls in BOUNDED for name in list_reads(cls)}
    assert {
        ("TasksStorageInterface", "read_open_places"),
        ("TenancyStorageInterface", "read_users_by_identity"),
        ("WorkStorageInterface", "requeue_stale"),
        ("BucketsInterface", "list"),
    } <= found


@pytest.mark.parametrize("interface", BOUNDED, ids=lambda cls: cls.__name__)
def test_every_list_read_takes_a_limit(interface: type) -> None:
    unbounded = [
        name
        for name in list_reads(interface)
        if "limit" not in signature(getattr(interface, name)).parameters
    ]
    assert not unbounded, f"{interface.__name__} lists with no limit: {unbounded}"
