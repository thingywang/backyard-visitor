"""Logical gift geometry shared by simulation and transparent Qt overlays.

Anchors refer to the final painted 144x112 cat canvas, including the artwork's
existing vertical adjustment. They must not receive another artwork offset.
Only window-supported poses add the shared six-pixel surface adjustment.
"""
from __future__ import annotations

CANVAS_WIDTH = 144.0
GIFT_SIZE = 38
CARRIED_GIFT_SIZE = float(GIFT_SIZE)
MOUTH_LEFT_X = 21.0
MOUTH_Y = 74.0
WINDOW_SURFACE_OFFSET = 6.0


def mouth_anchor(facing: int, *, surface_offset: float = 0.0) -> tuple[float, float]:
    """Return the carried object's center in the cat's local canvas."""
    return (MOUTH_LEFT_X if facing < 0 else CANVAS_WIDTH - MOUTH_LEFT_X,
            MOUTH_Y + surface_offset)


def carried_gift_rect(facing: int, *, surface_offset: float = 0.0) -> tuple[float, float, float, float]:
    """Return a Qt-independent (x, y, width, height) mouth overlay rectangle."""
    x, y = mouth_anchor(facing, surface_offset=surface_offset)
    half = CARRIED_GIFT_SIZE / 2
    return x-half, y-half, CARRIED_GIFT_SIZE, CARRIED_GIFT_SIZE


def drop_position(x: float, y: float, facing: int,
                  bounds: tuple[float, float, float, float], *,
                  surface_offset: float = 0.0) -> tuple[float, float]:
    """Place the 38px gift directly below the current mouth, fully on screen.

    Its top is at the carried object's center, so release moves the center down
    by 19px without moving it sideways. At display boundaries, constrain only
    as much as is necessary to keep the complete object available for pickup.
    """
    mouth_x, mouth_y = mouth_anchor(facing, surface_offset=surface_offset)
    left, top, width, height = bounds
    return (max(left, min(left+width-GIFT_SIZE, x+mouth_x-GIFT_SIZE/2)),
            max(top, min(top+height-GIFT_SIZE, y+mouth_y)))
