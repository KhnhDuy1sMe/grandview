# Cập nhật Guest Checkout – bước 1

Bản cập nhật này chuyển khách hàng sang đặt phòng không cần tài khoản. Admin và Lễ tân vẫn đăng nhập như trước.

## Thay đổi chính
- Bỏ giao diện đăng ký và đăng nhập khách hàng.
- Giữ cổng đăng nhập nhân viên trên trang chủ; chỉ `admin` và `receptionist` được đăng nhập.
- Khi chọn phòng, khách nhập họ tên và số điện thoại; email và ghi chú là tùy chọn.
- `POST /api/bookings` là API công khai, tạo bản ghi trong `customers` và liên kết bằng `bookings.customer_id`.
- Mã đặt phòng mới có 128 bit ngẫu nhiên (32 ký tự hex sau tiền tố `GV-`).
- Bảng `users` và đơn lịch sử không bị xóa. `bookings.user_id` nullable để tương thích dữ liệu cũ.
- Không triển khai OTP, SMS hoặc thanh toán trong bước này.

## Cập nhật trên máy đã có database
1. Sao lưu database trước khi thay đổi:

   ```powershell
   & "C:\Program Files\MySQL\MySQL Server 8.4\bin\mysqldump.exe" -u root -p hotel_management > hotel_management_backup.sql
   ```

2. Dừng FastAPI bằng `Ctrl + C`.
3. Sao chép các thư mục `app`, `frontend`, `database` và file `README.md` vào thư mục dự án; cho phép ghi đè. Giữ nguyên `.env` và `.venv`.
4. Chạy migration một lần (được thiết kế có thể chạy lại trên MySQL 8.4):

   ```powershell
   $schema = (Resolve-Path ".\database\migrations\001_guest_checkout.sql").Path.Replace('\','/')
   & "C:\Program Files\MySQL\MySQL Server 8.4\bin\mysql.exe" -u root -p -e "source $schema"
   ```

5. Khởi động lại FastAPI:

   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
   ```

Không chạy lại `database/schema.sql` trên database đang có dữ liệu. File đó dành cho database mới; với database hiện tại, dùng migration bên trên.

## Kiểm thử
- Trang chủ không còn yêu cầu khách đăng nhập/đăng ký.
- Khách tìm phòng, chọn một phòng và điền họ tên + số điện thoại; email và ghi chú có thể bỏ trống.
- Sau khi gửi, hệ thống hiển thị mã đặt phòng và giá dự kiến.
- Đăng nhập Admin hoặc Lễ tân vẫn hoạt động bằng tài khoản nhân viên hiện có.
- Vào giao diện quản lý, đơn mới phải hiển thị tên và số điện thoại người đặt.

## Chưa nằm trong bước này
Tra cứu booking, xác minh mã/số điện thoại, chỉnh sửa hoặc yêu cầu hủy booking sẽ được triển khai ở bước tiếp theo. Chưa có OTP hay thanh toán trực tuyến.
