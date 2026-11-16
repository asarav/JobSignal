"""S9: import-linter + domain has no I/O third-party imports."""

import importlib
import sys

FORBIDDEN_IO_MODULES = {
    "sqlite3",
    "httpx",
    "playwright",
    "fitz",
    "selectolax",
    "lxml",
    "bs4",
    "sklearn",
    "onnxruntime",
}


def test_domain_no_third_party_io_imports():
    """domain/ must not import sqlite3, httpx, playwright, etc."""
    importlib.import_module("app.domain")
    for mod_name in list(sys.modules):
        if mod_name == "app.domain" or mod_name.startswith("app.domain."):
            mod = sys.modules[mod_name]
            src = getattr(mod, "__file__", None)
            if not src:
                continue
            try:
                with open(src, encoding="utf-8") as handle:
                    text = handle.read()
            except OSError:
                continue
            for tok in FORBIDDEN_IO_MODULES:
                assert f"import {tok}" not in text and f"from {tok}" not in text, (
                    f"{mod_name} imports forbidden {tok}"
                )