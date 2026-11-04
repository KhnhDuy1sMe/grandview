-- Migration 002: bảng yêu cầu dịch vụ phòng (ăn uống, đặt xe, dọn phòng, spa)
-- Có thể chạy lại nhiều lần. Backend cũng tự tạo bảng này khi khởi động nếu tài khoản MySQL có quyền CREATE.
USE hotel_management;

CREATE TABLE IF NOT EXISTS service_requests (
    id INT AUTO_INCREMENT PRIMARY KEY,
    booking_id INT NOT NULL,
    room_id INT NOT NULL,
    category VARCHAR(30) NOT NULL,
    items_json TEXT NOT NULL,
    scheduled_at DATETIME NULL,
    destination VARCHAR(255) NULL,
    note VARCHAR(500) NULL,
    total_price DECIMAL(12, 2) NOT NULL DEFAULT 0,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    handled_by INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_service_booking (booking_id),
    INDEX idx_service_status (status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
