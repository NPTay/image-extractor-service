# image-extractor

> **Chức năng duy nhất**: Nhận file PDF → render từng trang thành ảnh PNG độ phân giải cao → trả JSON chứa base64.
> Service **không gọi AI, không lưu trữ lâu dài, không cần OpenAI key hay kết nối Qdrant/n8n** để chạy và test.

---

## Cách chạy

### Bằng Docker (khuyến nghị)

```bash
cp .env.example .env          # điều chỉnh nếu cần
docker-compose up --build
```

Container khởi động xong, kiểm tra:

```bash
curl http://localhost:8002/health
# → {"status":"ok"}
```

### Chạy local (development)

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8002
```

---

## Biến môi trường

| Biến | Mặc định | Ý nghĩa |
|------|----------|---------|
| `MAX_PDF_SIZE_MB` | `50` | Giới hạn dung lượng file PDF upload (MB) |
| `DEFAULT_DPI` | `200` | DPI mặc định nếu không truyền query param |
| `MAX_DPI` | `300` | DPI tối đa cho phép — nếu client truyền cao hơn sẽ bị **auto-clamp** về giá trị này (xem bên dưới) |
| `MAX_PIXELS_BEFORE_TILE` | `20000000` | Ngưỡng số pixel (width × height) của trang. Khi vượt ngưỡng, trang tự động bị chia tiles |
| `MAX_PAGES` | `100` | Giới hạn số trang PDF tối đa; vượt trả HTTP 413 |
| `PORT` | `8002` | Port expose ra ngoài |

### Giải thích `MAX_PIXELS_BEFORE_TILE`

Giá trị này quyết định trang nào sẽ bị chia nhỏ thành tiles:

- **20,000,000 px** (mặc định): phù hợp với khổ A4/A3 ở 200–300 DPI (không tile) và khổ A1/A0 sẽ tile.
- Nếu ảnh ra quá lớn cho Vision AI → **giảm ngưỡng** (ví dụ `10000000`) để chia nhỏ hơn.
- Nếu muốn không bao giờ tile → đặt rất cao (ví dụ `999999999`), nhưng cẩn thận RAM.
- Công thức kiểm tra: `width_pt × (dpi/72) × height_pt × (dpi/72)`. Ví dụ: A1 (1684×2384pt) ở 200dpi → 4678×6622 = ~30.97M px → vượt ngưỡng → tile.

---

## DPI nên dùng bao nhiêu?

| DPI | Khuyến nghị |
|-----|-------------|
| `200–300` | **Tốt nhất** cho bản vẽ kỹ thuật có chữ/số nhỏ, ký hiệu, kích thước |
| `150` | Chấp nhận được cho phác thảo, layout tổng thể |
| `< 150` | ⚠️ **Dễ làm mờ** ký hiệu, số kích thước — không khuyến nghị cho Vision AI |
| `> 300` | Auto-clamp về 300; tăng thêm ít lợi ích nhưng tăng mạnh RAM + thời gian xử lý |

---

## Chiến lược DPI vượt `MAX_DPI`

**Auto-clamp**: nếu `dpi` truyền vào > `MAX_DPI`, service tự động giảm về `MAX_DPI` và trả thêm field `"dpi_clamped": true` trong response. Không trả lỗi 400.

---

## API Reference

### `GET /health`

```bash
curl http://localhost:8002/health
```

```json
{"status": "ok"}
```

---

### `POST /extract-images`

```bash
curl -X POST http://localhost:8002/extract-images \
  -F "file=@/path/to/drawing.pdf" \
  -F "dpi=200"
```

**Ví dụ response** (phần `image_base64` rút gọn — trong response thực tế field này chứa chuỗi base64 đầy đủ của file PNG):

```json
{
  "file_id": "a3f5e21c8b...(sha256 hex)...",
  "filename": "drawing.pdf",
  "total_pages": 2,
  "dpi_requested": 200,
  "dpi_clamped": false,
  "failed_pages": [],
  "images": [
    {
      "page_number": 1,
      "tile_index": null,
      "tile_grid": null,
      "tile_bbox_in_page": null,
      "width_px": 1654,
      "height_px": 2339,
      "dpi_used": 200,
      "image_base64": "...(base64 rút gọn)..."
    },
    {
      "page_number": 2,
      "tile_index": 0,
      "tile_grid": "2x2",
      "tile_bbox_in_page": {"x0": 0.0, "y0": 0.0, "x1": 880.2, "y1": 1240.1},
      "width_px": 2445,
      "height_px": 3445,
      "dpi_used": 200,
      "image_base64": "...(base64 rút gọn)..."
    }
  ]
}
```

---

### `POST /extract-images/preview` *(debug only)*

Trả về ảnh PNG trực tiếp — không cần decode base64, mở thẳng trình duyệt/Postman:

```bash
# Xem trang 1 của PDF
curl -X POST "http://localhost:8002/extract-images/preview?page_number=1&dpi=150" \
  -F "file=@/path/to/drawing.pdf" \
  --output preview.png && open preview.png
```

Hoặc dùng Postman: gửi POST multipart, click "Send and Download".

---

## Giải thích field quan trọng cho tích hợp

| Field | Ý nghĩa |
|-------|---------|
| `file_id` | SHA-256 của raw PDF bytes (`hashlib.sha256(file_bytes).hexdigest()`). **Dùng làm khóa đối chiếu** — nếu service khác tính cùng SHA-256 từ cùng file sẽ ra cùng `file_id`. |
| `dpi_used` | DPI thực sự đã render (sau khi clamp). Dùng để quy đổi tọa độ pixel ↔ PDF points nếu cần. |
| `tile_index` | `null` = trang không tile (ảnh nguyên trang). Số `0, 1, 2...` = chỉ số tile trong grid. |
| `tile_grid` | `null` hoặc chuỗi kiểu `"2x3"` (cột×hàng). |
| `tile_bbox_in_page` | Tọa độ tile trong **hệ PDF point** của trang gốc (`x0, y0, x1, y1`). Dùng để ghép ngược lại vị trí thật trên bản vẽ. `null` nếu không tile. |
| `failed_pages` | Danh sách trang (1-based) bị lỗi khi render. Trang lỗi bị bỏ qua thay vì crash cả request. |

---

## Xử lý tiles khi tích hợp

Khi `tile_index != null`, một trang bản vẽ được chia thành nhiều ảnh. Để biết ảnh A nằm ở đâu trên bản vẽ gốc:

1. Lấy `tile_bbox_in_page` → tọa độ trong PDF points.
2. Nhân với `dpi_used / 72` để chuyển sang pixel trong hệ tọa độ full-page.
3. Các tile overlap ~5% biên — khi ghép ảnh chú ý cắt bỏ phần overlap.
