"""Standalone conveniences that do not touch the collection pipeline."""

import itertools

from loguru import logger

# QuPath stores class colours as signed 32-bit Java ints.
COLORS = {
    "red": 0xFF0000,
    "green": 0x00FF00,
    "blue": 0x0000FF,
    "magenta": 0xFF00FF,
    "cyan": 0x00FFFF,
    "yellow": 0xFFFF00,
}
JAVA_COLORS = [-(0x1000000 - rgb) for rgb in COLORS.values()]


def generate_combinations(first: list[str], second: list[str], replicates: int) -> list[str]:
    """Every combination of two categorical lists across a number of replicates."""
    if not all(isinstance(item, str) for item in first + second):
        raise TypeError("Both categorical inputs must be lists of strings")
    if not isinstance(replicates, int) or replicates < 1:
        raise ValueError("Replicates must be a positive integer")

    names = [
        f"{a}_{b}_{i}"
        for a, b, i in itertools.product(first, second, range(1, replicates + 1))
    ]
    logger.info(f"Generated {len(names)} class names")
    return names


def build_classes_json(names: list[str]) -> dict:
    """A QuPath `classes.json` payload for a list of class names."""
    return {
        "pathClasses": [
            {"name": name, "color": JAVA_COLORS[i % len(JAVA_COLORS)]} for i, name in enumerate(names)
        ]
    }
