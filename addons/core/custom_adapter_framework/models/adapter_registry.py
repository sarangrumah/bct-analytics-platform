# -*- coding: utf-8 -*-
from __future__ import annotations

_ADAPTER_REGISTRY: dict = {}


def register_adapter(name: str):
    def _wrap(cls):
        _ADAPTER_REGISTRY[name] = cls
        cls._adapter_name = name
        return cls

    return _wrap


def get_adapter_class(name: str):
    return _ADAPTER_REGISTRY.get(name)


def list_adapter_classes() -> list[str]:
    return sorted(_ADAPTER_REGISTRY.keys())


def unregister_adapter(name: str) -> None:
    _ADAPTER_REGISTRY.pop(name, None)
