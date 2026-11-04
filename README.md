# Grandview Hotel — FastAPI + MySQL

Bộ khung đồ án quản lý dịch vụ khách sạn. Frontend HTML/CSS/JavaScript hiện có được phục vụ trực tiếp bởi FastAPI; backend kết nối **trực tiếp tới MySQL bằng `mysql-connector-python`**, không dùng Flask và không dùng SQLAlchemy.

## 1. Chức năng có trong bản khởi đầu

- Kiểm tra API và kết nối MySQL: `GET /api/health`.
- Đăng ký tài khoản khách hàng, đăng nhập và lấy thông tin tài khoản hiện tại.
- Danh sách phòng; tìm phòng theo ngày nhận/trả phòng và số khách.
- Khách hàng tạo đơn đặt phòng; hệ thống tính số đêm và tổng giá dự kiến.
- Khách hàng xem lịch sử đặt phòng qua API.
- Lễ tân/quản trị viên xem đơn chờ và chuyển trạng thái đơn theo quy tắc nghiệp vụ.
- Lễ tân/quản trị viên cập nhật trạng thái vận hành của phòng.
- Quản trị viên lấy dữ liệu tổng quan dashboard.
- Trang web tìm phòng gọi API; khách có thể đăng nhập/đăng ký và gửi yêu cầu đặt phòng từ giao diện.

Các phần thanh toán trực tuyến, email/SMS thực, VR 360, CRUD đầy đủ các loại phòng và giao diện riêng cho lễ tân/chủ khách sạn chưa được triển khai trong bộ khung này.

## 2. Yêu cầu

- Python 3.10 trở lên.
- MySQL 8.x khuyến nghị.
- VS Code (hoặc IDE Python khác).

## 3. Thiết lập MySQL

### Cách A — MySQL Workbench

1. Mở MySQL Workbench và kết nối tới máy chủ MySQL.
2. Mở file `database/schema.sql` trong bộ dự án.
3. Chạy toàn bộ script để tạo database `hotel_management` và các bảng.
4. Để chạy nhanh trong môi trường local, có thể cấu hình `.env` dùng tài khoản MySQL mà bạn đang có quyền truy cập. Không dùng tài khoản quản trị MySQL cho môi trường triển khai thật.

Nếu muốn tạo tài khoản riêng cho ứng dụng, chạy trong Workbench bằng tài khoản có quyền quản trị (đổi mật khẩu mẫu trước khi dùng):

```sql
CREATE USER IF NOT EXISTS 'hotel_app'@'localhost' IDENTIFIED BY 'change_this_password';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX
ON hotel_management.* TO 'hotel_app'@'localhost';
FLUSH PRIVILEGES;
```

Nếu MySQL của bạn yêu cầu tài khoản theo host `127.0.0.1`, hãy tạo/grant user tương ứng cho host đó hoặc điều chỉnh `DB_HOST` trong `.env`.

## 4. Cài đặt và chạy trong VS Code (Windows)

Mở terminal tại thư mục `hotel_management_fastapi`.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Nếu PowerShell chặn kích hoạt môi trường ảo, bạn có thể dùng terminal Command Prompt:

```bat
.venv\Scripts\activate.bat
```

Tạo file `.env` bằng cách sao chép `.env.example`, sau đó sửa thông tin cho đúng với MySQL trên máy bạn:

```env
DB_HOST=127.0.0.1
DB_PORT=3306
DB_NAME=hotel_management
DB_USER=hotel_app
DB_PASSWORD=change_this_password
JWT_SECRET_KEY=put_a_long_random_secret_here
JWT_EXPIRE_MINUTES=120
```

Hãy thay `DB_USER`, `DB_PASSWORD` theo tài khoản đã tạo/thực sự có trên máy. `JWT_SECRET_KEY` nên là chuỗi ngẫu nhiên dài; không chia sẻ file `.env` và không đưa file này lên GitHub.

Sau khi đã chạy `database/schema.sql` và cài đặt `.env`, nạp dữ liệu mẫu:

```powershell
python -m app.seed
```

Script này thêm các loại phòng, 10 phòng mẫu và tài khoản quản trị demo nếu tài khoản đó chưa tồn tại. Tài khoản quản trị demo:

- Email: `admin@grandview.local`
- Mật khẩu: `Admin12345!`

Đây là thông tin dùng cho máy local để kiểm thử. Đổi mật khẩu trước khi trình diễn trên môi trường dùng chung. Đăng ký từ giao diện chỉ tạo vai trò `customer`; không thể tự đăng ký thành quản trị viên.

Khởi chạy server:

```powershell
uvicorn app.main:app --reload
```

Mở website tại <http://127.0.0.1:8000>.

## 5. Kiểm tra API

Mở <http://127.0.0.1:8000/docs> để xem và thử các endpoint trong Swagger UI.

- `GET /api/health`: kiểm tra kết nối database.
- `GET /api/rooms?guests=2`: liệt kê phòng hiện có thể sử dụng theo trạng thái vận hành.
- `GET /api/rooms?check_in=2026-11-01&check_out=2026-11-03&guests=2`: tìm phòng cho khoảng ngày cụ thể (hãy thay bằng ngày phù hợp, không ở quá khứ).
- `POST /api/auth/register`: tạo tài khoản khách hàng.
- `POST /api/auth/login`: đăng nhập và nhận bearer token.
- `GET /api/auth/me`: đọc tài khoản hiện tại, cần token.
- `POST /api/bookings`: gửi đơn đặt phòng, cần token khách hàng.
- `GET /api/bookings/my`: lịch sử đặt phòng của tài khoản hiện tại.
- `GET /api/bookings/pending`: các đơn chờ xử lý, cần vai trò `receptionist` hoặc `admin`.
- `PATCH /api/bookings/{booking_id}/status`: lễ tân/quản trị viên xác nhận hoặc từ chối đơn, thực hiện check-in/check-out hoặc hủy đơn theo trạng thái hợp lệ.
- `PATCH /api/rooms/{room_id}/operational-status?operational_status=cleaning`: cập nhật trạng thái vận hành phòng (vai trò lễ tân/quản trị viên).
- `GET /api/dashboard/summary`: thống kê dành cho `admin`.

Ví dụ body cho `POST /api/auth/register`:

```json
{
  "full_name": "Nguyen Van A",
  "email": "a@example.com",
  "password": "StrongPassword123"
}
```

Ví dụ body cho `POST /api/bookings` (gửi kèm `Authorization: Bearer <access_token>` của tài khoản khách hàng):

```json
{
  "room_id": 1,
  "check_in": "2026-11-01",
  "check_out": "2026-11-03",
  "guests": 2,
  "note": "Tôi muốn phòng yên tĩnh"
}
```

## 6. Cách kiểm tra nhanh

1. Chạy `python -m app.seed` không báo lỗi.
2. Chạy `uvicorn app.main:app --reload`.
3. Mở `/api/health`; kết quả kỳ vọng là `{"status":"ok","database":"connected"}`.
4. Mở trang chủ và thử tìm phòng.
5. Đăng ký một tài khoản khách hàng, chọn phòng và gửi yêu cầu.
6. Vào `/docs`, đăng nhập qua API, dùng tài khoản admin để kiểm tra endpoint dành cho lễ tân/quản lý. Lưu ý Swagger có nút **Authorize**; dán access token sau khi gọi API đăng nhập.

## 7. Lưu ý thiết kế

- Hệ thống kiểm tra đơn `pending`, `confirmed`, `checked_in` để tránh trùng phòng trong cùng khoảng ngày. Đơn `pending` cũng tạm giữ phòng trong giai đoạn chờ xử lý.
- Giá tổng dự kiến được tính bằng giá một đêm nhân số đêm; chưa có thuế, phí phụ thu hoặc thanh toán.
- Token frontend đang lưu trong `sessionStorage` để đơn giản hóa đồ án local. Với môi trường production cần rà soát bảo mật bổ sung, HTTPS, chính sách cookie/token, giới hạn đăng nhập và nhật ký thao tác.
- Giao diện khách hàng hiện dùng ảnh minh họa và URL Google Fonts/Unsplash; cần internet để tải các tài nguyên bên ngoài.
- Trạng thái vận hành (`available`, `occupied`, `cleaning`, `maintenance`) khác với khả năng đặt phòng theo ngày. Nghiệp vụ thực tế cần cập nhật trạng thái buồng phòng và lịch lưu trú đồng bộ hơn.
