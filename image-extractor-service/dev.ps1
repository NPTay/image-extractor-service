#!/usr/bin/env pwsh
# dev.ps1 — Quick start for development (builds + runs the service)
Write-Host "Starting image-extractor service..." -ForegroundColor Cyan
docker-compose up --build
