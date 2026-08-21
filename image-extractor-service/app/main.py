"""
main.py — FastAPI application for image-extractor service.

Routes:
  GET  /health                     → health check
  POST /extract-images             → PDF → list of base64 PNG images
  POST /extract-images/preview     → PDF → single PNG (StreamingResponse, debug only)
"""

from __future__ import annotations

import io
import logging

import fitz
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from app.config import get_settings
from app.extractor import extract_images_from_pdf
from app.models import ExtractImagesResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="image-extractor",
    description="Render PDF pages to high-resolution PNG images. No AI calls, no persistent storage.",
    version="1.0.0",
)


# ── /health ───────────────────────────────────────────────────────────────────

@app.get("/health")
def health_check() -> dict:
    return {"status": "ok"}


# ── /extract-images ───────────────────────────────────────────────────────────

@app.post("/extract-images", response_model=ExtractImagesResponse)
async def extract_images(
    file: UploadFile = File(..., description="PDF file to process"),
    dpi: int = Query(
        default=None,
        ge=1,
        description="Render DPI. Defaults to DEFAULT_DPI. Clamped to MAX_DPI if exceeded.",
    ),
) -> ExtractImagesResponse:
    settings = get_settings()
    dpi = dpi if dpi is not None else settings.default_dpi

    # Validate content type (best-effort from header)
    if file.content_type and "pdf" not in file.content_type.lower():
        raise HTTPException(
            status_code=400,
            detail=f"Expected a PDF file, got content-type: {file.content_type}",
        )

    file_bytes = await file.read()

    # Validate file size
    max_bytes = settings.max_pdf_size_mb * 1024 * 1024
    if len(file_bytes) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File size {len(file_bytes) // (1024*1024)} MB exceeds limit of {settings.max_pdf_size_mb} MB.",
        )

    # Validate PDF magic bytes
    if not file_bytes.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="File does not appear to be a valid PDF.")

    logger.info(
        "Processing '%s' — %.2f MB, DPI requested=%d",
        file.filename,
        len(file_bytes) / (1024 * 1024),
        dpi,
    )

    try:
        result = extract_images_from_pdf(
            file_bytes=file_bytes,
            filename=file.filename or "upload.pdf",
            dpi_requested=dpi,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except OverflowError as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    except Exception as exc:
        logger.error("Unexpected error during extraction: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error during PDF processing.")

    logger.info(
        "Done '%s': %d pages, %d images, %d failed, DPI used=%d%s",
        file.filename,
        result.total_pages,
        len(result.images),
        len(result.failed_pages),
        min(dpi, settings.max_dpi),
        " (clamped)" if result.dpi_clamped else "",
    )
    return result


# ── /extract-images/preview ───────────────────────────────────────────────────

@app.post("/extract-images/preview")
async def preview_page(
    file: UploadFile = File(..., description="PDF file"),
    page_number: int = Query(default=1, ge=1, description="1-based page number to preview"),
    dpi: int = Query(default=None, ge=1, description="Render DPI"),
) -> StreamingResponse:
    """Debug endpoint: returns a raw PNG (no base64) for quick visual inspection.

    Open directly in a browser or Postman. Not intended for production use.
    """
    settings = get_settings()
    dpi = dpi if dpi is not None else settings.default_dpi
    dpi = min(dpi, settings.max_dpi)

    file_bytes = await file.read()

    if not file_bytes.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="Not a valid PDF.")

    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Cannot open PDF: {exc}")

    total = doc.page_count
    if page_number > total:
        doc.close()
        raise HTTPException(
            status_code=404,
            detail=f"Page {page_number} not found. PDF has {total} page(s).",
        )

    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    page = doc[page_number - 1]
    pix = page.get_pixmap(matrix=matrix)
    png_bytes = pix.tobytes("png")
    del pix
    doc.close()

    return StreamingResponse(io.BytesIO(png_bytes), media_type="image/png")
