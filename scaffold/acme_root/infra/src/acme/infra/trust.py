"""Outbound TLS verification uses the operating system's trust store in
every process."""

import truststore

_installed = False


def install_trust_store() -> None:
    global _installed
    if _installed:
        return
    truststore.inject_into_ssl()
    _installed = True
