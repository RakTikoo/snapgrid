"""snapgrid — recover the true pixel grid from damaged 'fake pixel art'."""

__version__ = "0.1.1"

from .core import (  # noqa: F401
    AbstainError,
    GridEstimate,
    SnapError,
    estimate_grid,
    load_rgba,
    snap,
)
from .palette import apply_palette, build_palette  # noqa: F401
