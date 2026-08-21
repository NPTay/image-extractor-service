#!/usr/bin/env pwsh
# run-tests.ps1 — Build and run all unit tests inside Docker
Write-Host "=== Building test image ===" -ForegroundColor Cyan
docker build -f Dockerfile.test -t image-extractor-test .
if ($LASTEXITCODE -ne 0) { Write-Error "Build failed"; exit 1 }

Write-Host "`n=== Running pytest ===" -ForegroundColor Cyan
docker run --rm image-extractor-test
