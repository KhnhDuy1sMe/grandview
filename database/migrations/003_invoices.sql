-- Migration 003: bảng hóa đơn xuất khi trả phòng
-- Có thể chạy lại nhiều lần. Backend cũng tự tạo bảng này khi khởi động nếu tài khoản MySQL có quyền CREATE.
USE hotel_management;

CREATE TABLE IF NOT EXISTS invoices (
    id INT AUTO_INCREMENT PRIMARY KEY,
    booking_id INT NOT NULL,
    invoice_number VARCHAR(40) NOT NULL,
    room_amount DECIMAL(12, 2) NOT NULL DEFAULT 0,
    service_amount DECIMAL(12, 2) NOT NULL DEFAULT 0,
    total_amount DECIMAL(12, 2) NOT NULL DEFAULT 0,
    data_json MEDIUMTEXT NOT NULL,
    issued_by INT NULL,
    issued_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_invoice_booking (booking_id),
    UNIQUE KEY uq_invoice_number (invoice_number)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
