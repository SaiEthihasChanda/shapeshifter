"""Converters written from scratch: no third-party code, Python standard library only.

They sit in the same conversion graph as the library engines. With "prefer native"
enabled the planner picks them first; either way, if a native codec meets a variant it
doesn't handle (progressive JPEG, huge image, …) it raises Unsupported and the next
engine for that step takes over.
"""

from .raster import Frame, Raster, Unsupported

__all__ = ["Frame", "Raster", "Unsupported"]
