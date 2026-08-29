"""Stable offline corpus-pipeline import boundary.

The package exposes its four pipeline modules explicitly.  Keeping module
imports here avoids wildcard exports and makes the public surface predictable
for both the CLI and downstream jobs.
"""

from . import canonical, page_index, retrieval, tables

__all__ = ["canonical", "page_index", "retrieval", "tables"]
