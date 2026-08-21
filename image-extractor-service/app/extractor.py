"""
extractor.py — Core PDF → image rendering logic using PyMuPDF (fitz).

Tile hóa: khi tổng số pixel (width * height) của một trang ở DPI yêu cầu vượt
MAX_PIXELS_BEFORE_TILE, trang được chia thành lưới tiles (tự tính số hàng/cột
để mỗi tile dưới ngưỡng). Mỗi tile overlap biên ~5% để tránh cắt đứt chi tiết.
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import math
from typing import Optional

import fitz  # PyMuPDF

from app.config import get_settings
from app.models import ExtractImagesResponse, ImagePage

logger = logging.getLogger(__name__)


def _compute_file_id(file_bytes: bytes) -> str:
    """SHA-256 of raw PDF bytes.

    Formula: hashlib.sha256(file_bytes).hexdigest()
    Identical to the standard used across the pipeline so downstream services
    can match documents by the same deterministic key.
    """
    return hashlib.sha256(file_bytes).hexdigest()


def _pixmap_to_base64_png(pix: fitz.Pixmap) -> str:
    """Convert a PyMuPDF Pixmap to a base64-encoded PNG string."""
    png_bytes = pix.tobytes("png")
    return base64.b64encode(png_bytes).decode("ascii")


def _compute_tile_grid(page_w_pt: float, page_h_pt: float, max_pixels: int, dpi: int) -> tuple[int, int]:
    """
    Compute the (cols, rows) tile grid so that each tile stays under max_pixels.
    The grid preserves the page aspect ratio as closely as possible.

    Returns (cols, rows) with cols >= 1 and rows >= 1.
    """
    zoom = dpi / 72.0
    full_w_px = page_w_pt * zoom
    full_h_px = page_h_pt * zoom
    total_px = full_w_px * full_h_px

    # Minimum number of tiles needed
    n_tiles = math.ceil(total_px / max_pixels)

    if n_tiles <= 1:
        return 1, 1

    # Distribute tiles proportionally to width/height ratio to get squarish tiles
    aspect = full_w_px / full_h_px  # >1 means landscape
    # cols / rows ≈ aspect  →  cols = sqrt(n_tiles * aspect)
    cols = max(1, round(math.sqrt(n_tiles * aspect)))
    rows = math.ceil(n_tiles / cols)

    # Verify and increment if still over limit (edge cases with rounding)
    while True:
        tile_w = full_w_px / cols
        tile_h = full_h_px / rows
        if tile_w * tile_h <= max_pixels:
            break
        # Increase whichever dimension gives a more balanced grid
        if cols <= rows:
            cols += 1
        else:
            rows += 1

    return cols, rows


def _render_page(
    page: fitz.Page,
    dpi: int,
    max_pixels: int,
) -> list[ImagePage]:
    """
    Render one PDF page, applying tile hóa if the rendered size exceeds max_pixels.

    Returns a list of ImagePage objects (1 item when no tiling, N items when tiled).
    """
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)

    page_rect = page.rect  # in PDF points
    page_w_pt = page_rect.width
    page_h_pt = page_rect.height

    full_w_px = int(page_w_pt * zoom)
    full_h_px = int(page_h_pt * zoom)
    total_pixels = full_w_px * full_h_px

    page_num = page.number + 1  # 1-based

    if total_pixels <= max_pixels:
        # ── No tiling ──────────────────────────────────────────────────────────
        pix = page.get_pixmap(matrix=matrix)
        b64 = _pixmap_to_base64_png(pix)
        w, h = pix.width, pix.height
        del pix  # free memory immediately
        return [
            ImagePage(
                page_number=page_num,
                tile_index=None,
                tile_grid=None,
                tile_bbox_in_page=None,
                width_px=w,
                height_px=h,
                dpi_used=dpi,
                image_base64=b64,
            )
        ]

    # ── Tile hóa ───────────────────────────────────────────────────────────────
    cols, rows = _compute_tile_grid(page_w_pt, page_h_pt, max_pixels, dpi)
    grid_str = f"{cols}x{rows}"
    logger.info(
        "Page %d: %.0f×%.0f px exceeds limit (%d). Tiling into %s grid.",
        page_num,
        full_w_px,
        full_h_px,
        max_pixels,
        grid_str,
    )

    # Base tile size in PDF points
    tile_w_pt = page_w_pt / cols
    tile_h_pt = page_h_pt / rows

    # Overlap = 5% of tile size (in points)
    overlap_x = tile_w_pt * 0.05
    overlap_y = tile_h_pt * 0.05

    results: list[ImagePage] = []
    tile_idx = 0

    for row in range(rows):
        for col in range(cols):
            # Base coordinates without overlap
            x0 = col * tile_w_pt
            y0 = row * tile_h_pt
            x1 = x0 + tile_w_pt
            y1 = y0 + tile_h_pt

            # Expand by overlap (clamp to page boundaries)
            x0_ov = max(0.0, x0 - overlap_x)
            y0_ov = max(0.0, y0 - overlap_y)
            x1_ov = min(page_w_pt, x1 + overlap_x)
            y1_ov = min(page_h_pt, y1 + overlap_y)

            clip = fitz.Rect(x0_ov, y0_ov, x1_ov, y1_ov)
            pix = page.get_pixmap(matrix=matrix, clip=clip)
            b64 = _pixmap_to_base64_png(pix)
            w, h = pix.width, pix.height
            del pix  # free immediately

            results.append(
                ImagePage(
                    page_number=page_num,
                    tile_index=tile_idx,
                    tile_grid=grid_str,
                    tile_bbox_in_page={
                        "x0": round(x0_ov, 4),
                        "y0": round(y0_ov, 4),
                        "x1": round(x1_ov, 4),
                        "y1": round(y1_ov, 4),
                    },
                    width_px=w,
                    height_px=h,
                    dpi_used=dpi,
                    image_base64=b64,
                )
            )
            tile_idx += 1

    return results


def extract_images_from_pdf(
    file_bytes: bytes,
    filename: str,
    dpi_requested: int,
) -> ExtractImagesResponse:
    """
    Main extraction entry point.

    Clamps DPI to MAX_DPI if needed (auto-clamp strategy, no error raised).
    Processes pages sequentially to keep RAM usage proportional to one page at a time.
    """
    settings = get_settings()
    max_pixels = settings.max_pixels_before_tile

    dpi_clamped = dpi_requested > settings.max_dpi
    dpi = min(dpi_requested, settings.max_dpi)

    file_id = _compute_file_id(file_bytes)

    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"Cannot open PDF: {exc}") from exc

    if doc.needs_pass:
        doc.close()
        raise ValueError("PDF is encrypted/password-protected and cannot be processed.")

    total_pages = doc.page_count

    if total_pages > settings.max_pages:
        doc.close()
        raise OverflowError(
            f"PDF has {total_pages} pages which exceeds the limit of {settings.max_pages}."
        )

    all_images: list[ImagePage] = []
    failed_pages: list[int] = []

    for page_idx in range(total_pages):
        try:
            page = doc[page_idx]
            page_images = _render_page(page, dpi, max_pixels)
            all_images.extend(page_images)
        except Exception as exc:
            page_num = page_idx + 1
            logger.error("Failed to render page %d: %s", page_num, exc, exc_info=True)
            failed_pages.append(page_num)

    doc.close()

    return ExtractImagesResponse(
        file_id=file_id,
        filename=filename,
        total_pages=total_pages,
        dpi_requested=dpi_requested,
        dpi_clamped=dpi_clamped,
        images=all_images,
        failed_pages=failed_pages,
    )
