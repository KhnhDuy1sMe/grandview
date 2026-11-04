-- Migration 004: tiền đặt cọc và nhật ký thay đổi đơn
-- Có thể chạy lại nhiều lần. Backend cũng tự tạo các bảng này khi khởi động.
USE hotel_management;

CREATE TABLE IF NOT EXISTS booking_deposits (
    booking_id INT NOT NULL PRIMARY KEY,
    amount DECIMAL(12, 2) NOT NULL,
    method VARCHAR(20) NOT NULL,
    note VARCHAR(255) NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'paid',   -- paid | applied | refunded | forfeited
    received_by INT NULL,
    received_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    settled_at DATETIME NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS booking_changes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    booking_id INT NOT NULL,
    action VARCHAR(30) NOT NULL,
    detail VARCHAR(1000) NOT NULL,
    staff_id INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_change_booking (booking_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
