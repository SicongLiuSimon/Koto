#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PPT Themes & Master Slides Configuration
Defines the visual style, color palettes, and layout rules for Koto PPT Generator.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

RGB = Tuple[int, int, int]


@dataclass
class PPTTheme:
    name: str
    primary_color: RGB
    secondary_color: RGB
    background_color: RGB
    text_color: RGB
    accent_color: RGB

    title_font: str = "Microsoft YaHei"
    body_font: str = "Microsoft YaHei"
    code_font: str = "Consolas"

    # Optional layout assets
    bg_image: str = None
    logo_image: str = None

    # Layout specific configurations
    bullet_style: str = "•"


# Define Themes
THEMES = {
    "business": PPTTheme(
        name="Business Blue",
        primary_color=(41, 65, 122),  # Dark Blue
        secondary_color=(240, 244, 250),  # Light Blue Grey
        background_color=(255, 255, 255),  # White
        text_color=(51, 51, 51),  # Dark Grey
        accent_color=(228, 161, 27),  # Gold
    ),
    "tech": PPTTheme(
        name="Modern Tech",
        primary_color=(0, 120, 215),  # Tech Blue
        secondary_color=(30, 30, 30),  # Dark Grey
        background_color=(20, 20, 25),  # Nearly Black
        text_color=(240, 240, 240),  # White Text
        accent_color=(0, 255, 150),  # Neon Green
        title_font="Segoe UI",
        body_font="Segoe UI Local",
    ),
    "minimal": PPTTheme(
        name="Minimalist",
        primary_color=(0, 0, 0),
        secondary_color=(245, 245, 245),
        background_color=(255, 255, 255),
        text_color=(30, 30, 30),
        accent_color=(255, 78, 80),  # Coral Red
        title_font="Arial",
    ),
    "creative": PPTTheme(
        name="Creative Gradient",
        primary_color=(138, 43, 226),  # Purple
        secondary_color=(255, 240, 245),
        background_color=(255, 255, 255),
        text_color=(60, 20, 80),
        accent_color=(255, 165, 0),  # Orange
    ),
}


def get_theme(name: str) -> PPTTheme:
    return THEMES.get(name, THEMES["business"])
