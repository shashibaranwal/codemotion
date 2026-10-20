"""CodeMotion: turn a Python video script into a short motion-graphic video.

Video scripts import the API from here::

    from codemotion import Video, Text, Code, Image, Shape, Line, Terminal, Group, ease, stagger
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

# CodeMotion never uses BLAS; numpy's OpenBLAS otherwise commits ~30 MB per core at import
# (~400 MB on a 12-core PC), in every render worker. Must run before numpy is imported.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

__version__ = "0.5.0"
__all__ = [
    "Code",
    "Group",
    "Image",
    "Line",
    "ScriptError",
    "Shape",
    "Terminal",
    "Text",
    "Video",
    "ease",
    "stagger",
]

if TYPE_CHECKING:
    from codemotion.script.api import (
        Code,
        Group,
        Image,
        Line,
        ScriptError,
        Shape,
        Terminal,
        Text,
        Video,
        ease,
        stagger,
    )


def __getattr__(name: str) -> Any:
    # Lazy, so tools that only need e.g. ``codemotion.config`` don't load the API.
    if name in __all__:
        from codemotion.script import api

        return getattr(api, name)
    raise AttributeError(f"module 'codemotion' has no attribute {name!r}")
