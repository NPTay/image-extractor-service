FROM python:3.11-slim

WORKDIR /app

# System dependencies for PyMuPDF (fitz) rendering on slim images.
# PyMuPDF >= 1.23 bundles its own MuPDF C libs, but still needs:
#   - libglib2.0-0, libgl1, libgomp1  : runtime linking for image ops
#   - libjpeg62-turbo, libopenjp2-7   : JPEG / JPEG2000 image support
#   - libtiff6, libfreetype6          : TIFF images and font rendering
#   - libfontconfig1                  : font lookup (text overlay)
#   - curl                            : Docker healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 \
        libgl1 \
        libgomp1 \
        libjpeg62-turbo \
        libopenjp2-7 \
        libtiff6 \
        libfreetype6 \
        libfontconfig1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8002

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]
