-- 005: cấu trúc phòng và bộ ảnh cho từng hạng phòng.
-- Chạy trên cơ sở dữ liệu đã có; cài mới thì schema.sql đã bao gồm các cột này.
USE hotel_management;

ALTER TABLE room_types
  ADD COLUMN area_sqm SMALLINT UNSIGNED NULL AFTER image_url,
  ADD COLUMN bed_type VARCHAR(80) NULL AFTER area_sqm,
  ADD COLUMN view_name VARCHAR(80) NULL AFTER bed_type,
  ADD COLUMN layout_summary VARCHAR(255) NULL AFTER view_name,
  ADD COLUMN amenities VARCHAR(600) NULL AFTER layout_summary,
  ADD COLUMN gallery_json TEXT NULL AFTER amenities;

-- Dữ liệu mẫu: chạy `python -m app.seed` để điền cấu trúc và ảnh tạm.
