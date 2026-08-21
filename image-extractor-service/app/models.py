from __future__ import annotations
from typing import Optional
from pydantic import BaseModel


class ImagePage(BaseModel):
    """Represents one rendered image (full page or a tile of a page)."""

    page_number: int
    tile_index: Optional[int] = None
    tile_grid: Optional[str] = None
    tile_bbox_in_page: Optional[dict] = None  # {"x0", "y0", "x1", "y1"} in PDF points
    width_px: int
    height_px: int
    dpi_used: int
    image_base64: str


class ExtractImagesResponse(BaseModel):
    """Top-level response from /extract-images.

    file_id is SHA-256 of the raw PDF bytes (hashlib.sha256(file_bytes).hexdigest()),
    identical to the standard formula so downstream services can match by the same key.
    """

    file_id: str
    filename: str
    total_pages: int
    dpi_requested: int
    dpi_clamped: bool = False          # True when dpi_requested > MAX_DPI and was auto-clamped
    images: list[ImagePage]
    failed_pages: list[int] = []
