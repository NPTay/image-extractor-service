"""
tests/test_extract.py — pytest test suite for image-extractor service.

Uses FastAPI TestClient (via httpx) — no Docker required for unit testing.
A minimal single-page PDF is generated on-the-fly using PyMuPDF so the test
suite has zero external file dependencies.
"""

from __future__ import annotations

import base64
import io
import os

import fitz
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app

client = TestClient(app)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_sample_pdf(pages: int = 1, width_pt: float = 595, height_pt: float = 842) -> bytes:
    """Generate a minimal in-memory PDF with `pages` pages of the given size."""
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=width_pt, height=height_pt)
        page.insert_text(
            (50, 100 + i * 20),
            f"Test page {i + 1}",
            fontsize=24,
        )
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _make_large_pdf_bytes() -> bytes:
    """Make a PDF page sized like A1 (841×1189mm) which at 200dpi will exceed 20M pixels."""
    # A1 in points: 1684 x 2384 pt  → at 200dpi: (1684*200/72) * (2384*200/72) ≈ 32M px
    return _make_sample_pdf(pages=1, width_pt=1684, height_pt=2384)


# ── Phase 1 — /health ─────────────────────────────────────────────────────────

def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ── Phase 2 — /extract-images basic ─────────────────────────────────────────

class TestExtractImages:
    def test_single_page_no_tile(self):
        pdf = _make_sample_pdf(pages=1)
        resp = client.post(
            "/extract-images",
            files={"file": ("test.pdf", pdf, "application/pdf")},
            params={"dpi": 150},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total_pages"] == 1
        assert len(data["images"]) == 1
        img = data["images"][0]
        assert img["page_number"] == 1
        assert img["tile_index"] is None
        assert img["tile_grid"] is None
        assert img["dpi_used"] == 150

    def test_multi_page(self):
        pdf = _make_sample_pdf(pages=3)
        resp = client.post(
            "/extract-images",
            files={"file": ("multi.pdf", pdf, "application/pdf")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_pages"] == 3
        # No tiling expected for A4 at default 200dpi (≈ 1656*2338 = ~3.9M < 20M)
        assert len(data["images"]) == 3
        for idx, img in enumerate(data["images"]):
            assert img["page_number"] == idx + 1
            assert img["tile_index"] is None

    def test_image_base64_is_valid_png(self):
        pdf = _make_sample_pdf(pages=1)
        resp = client.post(
            "/extract-images",
            files={"file": ("test.pdf", pdf, "application/pdf")},
            params={"dpi": 72},
        )
        assert resp.status_code == 200
        b64 = resp.json()["images"][0]["image_base64"]
        img_bytes = base64.b64decode(b64)
        img = Image.open(io.BytesIO(img_bytes))
        img.verify()  # raises if not valid image

    def test_file_id_is_stable(self):
        pdf = _make_sample_pdf(pages=1)
        r1 = client.post("/extract-images", files={"file": ("a.pdf", pdf, "application/pdf")})
        r2 = client.post("/extract-images", files={"file": ("a.pdf", pdf, "application/pdf")})
        assert r1.json()["file_id"] == r2.json()["file_id"]

    def test_dpi_clamp(self):
        """DPI above MAX_DPI (300) should be clamped, dpi_clamped=True, dpi_used=300."""
        pdf = _make_sample_pdf(pages=1)
        resp = client.post(
            "/extract-images",
            files={"file": ("test.pdf", pdf, "application/pdf")},
            params={"dpi": 999},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["dpi_clamped"] is True
        assert data["images"][0]["dpi_used"] == 300

    def test_invalid_not_pdf(self):
        fake = b"This is not a PDF file at all."
        resp = client.post(
            "/extract-images",
            files={"file": ("fake.pdf", fake, "application/pdf")},
        )
        assert resp.status_code == 400

    def test_wrong_content_type(self):
        pdf = _make_sample_pdf()
        resp = client.post(
            "/extract-images",
            files={"file": ("img.png", pdf, "image/png")},
        )
        assert resp.status_code == 400

    def test_file_too_large(self, monkeypatch):
        from app import config as cfg
        settings = cfg.get_settings()
        monkeypatch.setattr(settings, "max_pdf_size_mb", 0)  # force 0 MB limit
        # Force cache refresh
        cfg.get_settings.cache_clear()
        monkeypatch.setattr(cfg, "get_settings", lambda: settings)

        pdf = _make_sample_pdf()
        resp = client.post(
            "/extract-images",
            files={"file": ("big.pdf", pdf, "application/pdf")},
        )
        # Restore
        cfg.get_settings.cache_clear()
        assert resp.status_code == 413


# ── Tile hóa tests ────────────────────────────────────────────────────────────

class TestTiling:
    def test_large_page_triggers_tiling(self, monkeypatch):
        """A1-sized page at 200dpi should trigger tiling with default 20M pixel limit."""
        pdf = _make_large_pdf_bytes()
        resp = client.post(
            "/extract-images",
            files={"file": ("large.pdf", pdf, "application/pdf")},
            params={"dpi": 200},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        images = data["images"]
        # There must be more than 1 image for this single page (tiling occurred)
        assert len(images) > 1, "Expected tiling but got only 1 image"
        for img in images:
            assert img["tile_index"] is not None
            assert img["tile_grid"] is not None
            assert img["tile_bbox_in_page"] is not None

    def test_tile_bbox_coverage(self, monkeypatch):
        """The union of all tile bboxes must cover the full page area (with possible overlap)."""
        pdf = _make_large_pdf_bytes()
        resp = client.post(
            "/extract-images",
            files={"file": ("large.pdf", pdf, "application/pdf")},
            params={"dpi": 200},
        )
        assert resp.status_code == 200
        images = resp.json()["images"]

        # A1 page in points: 1684 x 2384
        PAGE_W = 1684.0
        PAGE_H = 2384.0

        for img in images:
            bbox = img["tile_bbox_in_page"]
            assert bbox["x0"] >= 0
            assert bbox["y0"] >= 0
            assert bbox["x1"] <= PAGE_W + 1  # +1 for floating-point tolerance
            assert bbox["y1"] <= PAGE_H + 1

        # The last tile in each row/col should reach the page boundary
        x1_max = max(img["tile_bbox_in_page"]["x1"] for img in images)
        y1_max = max(img["tile_bbox_in_page"]["y1"] for img in images)
        assert x1_max == pytest.approx(PAGE_W, abs=1.0)
        assert y1_max == pytest.approx(PAGE_H, abs=1.0)


# ── Phase 3 — /extract-images/preview ────────────────────────────────────────

class TestPreview:
    def test_preview_returns_png(self):
        pdf = _make_sample_pdf(pages=2)
        resp = client.post(
            "/extract-images/preview",
            files={"file": ("test.pdf", pdf, "application/pdf")},
            params={"page_number": 1, "dpi": 72},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        img = Image.open(io.BytesIO(resp.content))
        assert img.format == "PNG"

    def test_preview_page_out_of_range(self):
        pdf = _make_sample_pdf(pages=1)
        resp = client.post(
            "/extract-images/preview",
            files={"file": ("test.pdf", pdf, "application/pdf")},
            params={"page_number": 99},
        )
        assert resp.status_code == 404
