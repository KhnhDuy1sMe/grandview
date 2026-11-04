from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import json
import secrets
from typing import Any, Literal

import jwt
import mysql.connector
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from mysql.connector import MySQLConnection
from pydantic import BaseModel, Field

from app import config
from app.db import connect_db, get_db
from app.schemas import (
    BookingInput,
    BookingLookupInput,
    BookingLookupOutput,
    BookingOutput,
    BookingStatusInput,
    LoginInput,
    RoomOutput,
    UserOutput,
    WalkInInput,
)
from app.security import (
    create_access_token,
    require_roles,
    verify_password,
)

app = FastAPI(
    title="Hotel Management API",
    description="API nền tảng cho website quản lý dịch vụ khách sạn của nhóm.",
    version="0.1.0",
)

BLOCKING_BOOKING_STATUSES = ("pending", "confirmed", "checked_in")

# Điều kiện một phòng còn bán được (dùng chung cho mọi truy vấn tìm phòng trống).
# Phòng bảo trì thì không bán. Phòng bị lễ tân đánh dấu "đang có khách" mà không có
# lượt lưu trú nào cũng không bán, vì chưa biết khách ở đến bao giờ: lễ tân cần
# thêm khách tại quầy để hệ thống biết ngày trả phòng.
ROOM_SELLABLE_SQL = """r.operational_status <> 'maintenance'
                     AND (r.operational_status <> 'occupied' OR EXISTS (
                             SELECT 1 FROM bookings bk
                             WHERE bk.room_id = r.id AND bk.status = 'checked_in'))"""


def rows_as_dict(cursor) -> list[dict[str, Any]]:
    return list(cursor.fetchall())


def booking_row_to_output(row: dict[str, Any]) -> dict[str, Any]:
    if row.get("created_at") and not isinstance(row["created_at"], str):
        row["created_at"] = row["created_at"].isoformat(sep=" ")
    if "service_total" in row:
        row["service_total"] = Decimal(str(row["service_total"] or 0))
        row["grand_total"] = Decimal(str(row["total_price"])) + row["service_total"]
    if "deposit_amount" in row:
        paid = row.get("deposit_status") in ("paid", "applied")
        row["deposit_paid"] = Decimal(str(row["deposit_amount"])) if paid and row["deposit_amount"] is not None else Decimal("0")
        if "grand_total" in row:
            row["amount_due"] = row["grand_total"] - row["deposit_paid"]
        if row.get("deposit_received_at") and not isinstance(row["deposit_received_at"], str):
            row["deposit_received_at"] = row["deposit_received_at"].isoformat(sep=" ", timespec="minutes")
    return row


ROOM_TYPE_DETAIL_COLUMNS = """rt.area_sqm, rt.bed_type, rt.view_name,
                      rt.layout_summary, rt.amenities, rt.gallery_json"""


def room_type_with_layout(row: dict[str, Any]) -> dict[str, Any]:
    """Tách chuỗi tiện nghi và danh sách ảnh để trang chủ hiển thị cấu trúc phòng."""
    amenities = [part.strip() for part in (row.pop("amenities", None) or "").split(",") if part.strip()]
    try:
        gallery = json.loads(row.pop("gallery_json", None) or "[]")
    except (TypeError, ValueError):
        gallery = []
    if not isinstance(gallery, list):
        gallery = []
    gallery = [str(item) for item in gallery if item]
    if row.get("image_url") and row["image_url"] not in gallery:
        gallery.insert(0, row["image_url"])
    row["amenities"] = amenities
    row["gallery"] = gallery
    return row


def mask_phone_number(value: str | None) -> str | None:
    """Return a masked phone number for public booking lookup responses."""
    if not value:
        return None
    digits = "".join(character for character in value if character.isdigit())
    if len(digits) <= 4:
        return "*" * len(digits)
    return f"{digits[:2]}{'*' * max(3, len(digits) - 4)}{digits[-2:]}"


@app.get("/api/health")
def health_check():
    try:
        connection = connect_db()
        cursor = connection.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        cursor.close()
        connection.close()
        return {"status": "ok", "database": "connected"}
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=503, detail="API chạy nhưng chưa kết nối được MySQL.") from exc


@app.post("/api/auth/login")
def login(payload: LoginInput, connection: MySQLConnection = Depends(get_db)):
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(
            """SELECT id, full_name, email, password_hash, role, is_active
               FROM users WHERE email = %s LIMIT 1""",
            (payload.email.lower(),),
        )
        user = cursor.fetchone()
    finally:
        cursor.close()

    if not user or not user["is_active"] or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Email hoặc mật khẩu không chính xác.")
    if user["role"] not in {"admin", "receptionist"}:
        raise HTTPException(status_code=403, detail="Đăng nhập chỉ dành cho tài khoản Lễ tân và Quản trị viên.")

    token = create_access_token(user["id"], user["role"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user["id"],
            "full_name": user["full_name"],
            "email": user["email"],
            "role": user["role"],
        },
    }


@app.get("/api/auth/me", response_model=UserOutput)
def read_current_user(user: dict[str, Any] = Depends(require_roles("admin", "receptionist"))):
    return user


@app.get("/api/room-types")
def list_room_types(connection: MySQLConnection = Depends(get_db)):
    """Các hạng phòng cho trang chủ. Không trả số phòng; số phòng chỉ hiện khi khách tìm phòng trống theo ngày."""
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(
            """SELECT rt.id, rt.name, rt.description, rt.base_price AS price_per_night,
                      rt.max_guests, rt.image_url, """ + ROOM_TYPE_DETAIL_COLUMNS + """,
                      COUNT(r.id) AS room_count
               FROM room_types rt
               JOIN rooms r ON r.room_type_id = rt.id AND r.is_active = TRUE
               WHERE rt.is_active = TRUE
               GROUP BY rt.id, rt.name, rt.description, rt.base_price, rt.max_guests, rt.image_url,
                        rt.area_sqm, rt.bed_type, rt.view_name, rt.layout_summary, rt.amenities, rt.gallery_json
               ORDER BY rt.base_price"""
        )
        return [room_type_with_layout(row) for row in rows_as_dict(cursor)]
    finally:
        cursor.close()


@app.get("/api/rooms", response_model=list[RoomOutput])
def list_rooms(
    check_in: date | None = None,
    check_out: date | None = None,
    guests: int = Query(default=1, ge=1, le=20),
    connection: MySQLConnection = Depends(get_db),
    _staff: dict[str, Any] = Depends(require_roles("receptionist", "admin")),
):
    """Danh sách từng phòng kèm số phòng, chỉ dành cho nhân viên. Khách dùng /api/room-availability."""
    if (check_in is None) != (check_out is None):
        raise HTTPException(status_code=400, detail="Cần truyền cả ngày nhận phòng và ngày trả phòng.")
    if check_in and check_out:
        if check_out <= check_in:
            raise HTTPException(status_code=400, detail="Ngày trả phòng phải sau ngày nhận phòng.")
        if check_in < date.today():
            raise HTTPException(status_code=400, detail="Ngày nhận phòng không thể ở trong quá khứ.")

    cursor = connection.cursor(dictionary=True)
    try:
        if check_in and check_out:
            cursor.execute(
                """SELECT r.id, r.room_number, r.floor, r.operational_status,
                          rt.id AS room_type_id, rt.name AS room_type, rt.description,
                          rt.base_price AS price_per_night, rt.max_guests, rt.image_url
                   FROM rooms r
                   JOIN room_types rt ON rt.id = r.room_type_id
                   WHERE r.is_active = TRUE AND rt.is_active = TRUE
                     AND """ + ROOM_SELLABLE_SQL + """
                     AND rt.max_guests >= %s
                     AND NOT EXISTS (
                         SELECT 1 FROM bookings b
                         WHERE b.room_id = r.id
                           AND b.status IN ('pending', 'confirmed', 'checked_in')
                           AND b.check_in < %s AND b.check_out > %s
                     )
                   ORDER BY rt.base_price, r.room_number""",
                (guests, check_out, check_in),
            )
        else:
            cursor.execute(
                """SELECT r.id, r.room_number, r.floor, r.operational_status,
                          rt.id AS room_type_id, rt.name AS room_type, rt.description,
                          rt.base_price AS price_per_night, rt.max_guests, rt.image_url
                   FROM rooms r
                   JOIN room_types rt ON rt.id = r.room_type_id
                   WHERE r.is_active = TRUE AND rt.is_active = TRUE
                     AND r.operational_status = 'available'
                     AND rt.max_guests >= %s
                   ORDER BY rt.base_price, r.room_number""",
                (guests,),
            )
        return rows_as_dict(cursor)
    finally:
        cursor.close()


@app.get("/api/staff/rooms")
def staff_rooms(
    connection: MySQLConnection = Depends(get_db),
    _staff: dict[str, Any] = Depends(require_roles("receptionist", "admin")),
):
    """Return every active room for the staff room-status board."""
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(
            """SELECT r.id, r.room_number, r.floor,
                      CASE
                          WHEN r.operational_status = 'available' AND EXISTS (
                              SELECT 1 FROM bookings b
                              WHERE b.room_id = r.id AND b.status = 'checked_in'
                          ) THEN 'occupied'
                          ELSE r.operational_status
                      END AS operational_status,
                      rt.name AS room_type, rt.base_price AS price_per_night,
                      rt.max_guests,
                      cur.booking_code AS current_booking_code,
                      cur.customer_name AS current_guest_name,
                      cur.customer_phone AS current_guest_phone,
                      cur.check_out AS current_check_out
               FROM rooms r
               JOIN room_types rt ON rt.id = r.room_type_id
               LEFT JOIN (
                   SELECT b.room_id, b.booking_code, b.check_out,
                          COALESCE(c.full_name, u.full_name) AS customer_name,
                          c.phone AS customer_phone
                   FROM bookings b
                   LEFT JOIN customers c ON c.id = b.customer_id
                   LEFT JOIN users u ON u.id = b.user_id
                   WHERE b.status = 'checked_in'
               ) cur ON cur.room_id = r.id
               WHERE r.is_active = TRUE AND rt.is_active = TRUE
               ORDER BY r.floor, r.room_number"""
        )
        return rows_as_dict(cursor)
    finally:
        cursor.close()


class GuestBookingInput(BookingInput):
    """Khách đặt theo hạng phòng (room_type_id); hệ thống tự xếp phòng, khách không thấy số phòng."""
    room_id: int | None = Field(default=None, gt=0)
    room_type_id: int | None = Field(default=None, gt=0)


def find_free_room(cursor, room_type_id: int, check_in: date, check_out: date,
                   exclude_booking_id: int | None = None) -> dict[str, Any] | None:
    """Khóa các phòng của hạng rồi chọn phòng trống đầu tiên trong khoảng ngày."""
    cursor.execute(
        """SELECT r.id, r.room_number, rt.base_price, rt.max_guests, rt.name AS room_type
           FROM rooms r JOIN room_types rt ON rt.id = r.room_type_id
           WHERE rt.id = %s AND r.is_active = TRUE AND rt.is_active = TRUE
             AND """ + ROOM_SELLABLE_SQL + """
           ORDER BY r.room_number
           FOR UPDATE""",
        (room_type_id,),
    )
    candidates = rows_as_dict(cursor)
    for room in candidates:
        cursor.execute(
            """SELECT id FROM bookings
               WHERE room_id = %s AND status IN ('pending', 'confirmed', 'checked_in')
                 AND check_in < %s AND check_out > %s AND id <> %s
               LIMIT 1""",
            (room["id"], check_out, check_in, exclude_booking_id or 0),
        )
        if not cursor.fetchone():
            return room
    return None


@app.get("/api/room-availability")
def room_availability(
    check_in: date,
    check_out: date,
    guests: int = Query(default=1, ge=1, le=20),
    connection: MySQLConnection = Depends(get_db),
):
    """Khách tìm phòng: trả về hạng phòng còn trống và số lượng, không trả số phòng."""
    if check_out <= check_in:
        raise HTTPException(status_code=400, detail="Ngày trả phòng phải sau ngày nhận phòng.")
    if check_in < date.today():
        raise HTTPException(status_code=400, detail="Ngày nhận phòng không thể ở trong quá khứ.")
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(
            """SELECT rt.id, rt.name, rt.description, rt.base_price AS price_per_night,
                      rt.max_guests, rt.image_url, """ + ROOM_TYPE_DETAIL_COLUMNS + """,
                      COUNT(r.id) AS available_count
               FROM room_types rt
               JOIN rooms r ON r.room_type_id = rt.id
               WHERE r.is_active = TRUE AND rt.is_active = TRUE
                 AND """ + ROOM_SELLABLE_SQL + """
                 AND rt.max_guests >= %s
                 AND NOT EXISTS (
                     SELECT 1 FROM bookings b
                     WHERE b.room_id = r.id
                       AND b.status IN ('pending', 'confirmed', 'checked_in')
                       AND b.check_in < %s AND b.check_out > %s
                 )
               GROUP BY rt.id, rt.name, rt.description, rt.base_price, rt.max_guests, rt.image_url,
                        rt.area_sqm, rt.bed_type, rt.view_name, rt.layout_summary, rt.amenities, rt.gallery_json
               ORDER BY rt.base_price""",
            (guests, check_out, check_in),
        )
        return [room_type_with_layout(row) for row in rows_as_dict(cursor)]
    finally:
        cursor.close()


@app.post("/api/bookings", status_code=status.HTTP_201_CREATED)
def create_booking(
    payload: GuestBookingInput,
    connection: MySQLConnection = Depends(get_db),
):
    if payload.check_in < date.today():
        raise HTTPException(status_code=400, detail="Ngày nhận phòng không thể ở trong quá khứ.")
    if not payload.room_type_id and not payload.room_id:
        raise HTTPException(status_code=400, detail="Vui lòng chọn hạng phòng.")

    cursor = connection.cursor(dictionary=True)
    try:
        connection.start_transaction()
        if payload.room_type_id:
            free_room = find_free_room(cursor, payload.room_type_id, payload.check_in, payload.check_out)
            if not free_room:
                raise HTTPException(
                    status_code=409,
                    detail="Hạng phòng này vừa hết phòng trống cho ngày bạn chọn. Hãy chọn hạng phòng khác hoặc đổi ngày.",
                )
            payload.room_id = free_room["id"]
        # Lock the room row so two simultaneous requests cannot both book the same room.
        cursor.execute(
            """SELECT r.id, r.operational_status, rt.base_price, rt.max_guests
               FROM rooms r JOIN room_types rt ON rt.id = r.room_type_id
               WHERE r.id = %s AND r.is_active = TRUE AND rt.is_active = TRUE
               FOR UPDATE""",
            (payload.room_id,),
        )
        room = cursor.fetchone()
        if not room:
            connection.rollback()
            raise HTTPException(status_code=404, detail="Không tìm thấy phòng.")
        if room["operational_status"] == "maintenance":
            connection.rollback()
            raise HTTPException(status_code=409, detail="Phòng đang bảo trì, không thể đặt.")
        if room["operational_status"] == "occupied":
            cursor.execute(
                "SELECT id FROM bookings WHERE room_id = %s AND status = 'checked_in' LIMIT 1",
                (payload.room_id,),
            )
            if not cursor.fetchone():
                connection.rollback()
                raise HTTPException(
                    status_code=409,
                    detail="Phòng này đang có khách tại quầy. Hãy chọn phòng khác hoặc đổi ngày.",
                )
        if payload.guests > room["max_guests"]:
            connection.rollback()
            raise HTTPException(status_code=400, detail=f"Phòng chỉ phục vụ tối đa {room['max_guests']} khách.")

        cursor.execute(
            """SELECT id FROM bookings
               WHERE room_id = %s AND status IN ('pending', 'confirmed', 'checked_in')
                 AND check_in < %s AND check_out > %s
               LIMIT 1 FOR UPDATE""",
            (payload.room_id, payload.check_out, payload.check_in),
        )
        if cursor.fetchone():
            connection.rollback()
            raise HTTPException(status_code=409, detail="Phòng vừa được đặt cho khoảng thời gian này. Hãy tìm phòng khác.")

        nights = (payload.check_out - payload.check_in).days
        total_price = room["base_price"] * nights
        # 128-bit random secret; unlike a short sequential code, it is impractical to guess.
        booking_code = f"GV-{secrets.token_hex(16).upper()}"
        cursor.execute(
            """INSERT INTO customers (full_name, phone, email)
               VALUES (%s, %s, %s)""",
            (payload.customer_name, payload.customer_phone, payload.customer_email),
        )
        customer_id = cursor.lastrowid
        cursor.execute(
            """INSERT INTO bookings
               (booking_code, user_id, customer_id, room_id, check_in, check_out, guests, total_price, note, status)
               VALUES (%s, NULL, %s, %s, %s, %s, %s, %s, %s, 'pending')""",
            (
                booking_code,
                customer_id,
                payload.room_id,
                payload.check_in,
                payload.check_out,
                payload.guests,
                total_price,
                payload.note,
            ),
        )
        booking_id = cursor.lastrowid
        cursor.execute("SELECT rt.name FROM rooms r JOIN room_types rt ON rt.id = r.room_type_id WHERE r.id = %s", (payload.room_id,))
        room_type_name = cursor.fetchone()["name"]
        connection.commit()
        # Không trả id/số phòng cho khách: lễ tân thấy số phòng trong trang quản lý.
        return {
            "booking_code": booking_code,
            "customer_name": payload.customer_name,
            "room_type": room_type_name,
            "check_in": payload.check_in,
            "check_out": payload.check_out,
            "guests": payload.guests,
            "total_price": total_price,
            "note": payload.note,
            "status": "pending",
            "created_at": datetime.now().isoformat(sep=" "),
        }
    except HTTPException:
        connection.rollback()
        raise
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


@app.post("/api/bookings/lookup", response_model=BookingLookupOutput)
def lookup_booking(
    payload: BookingLookupInput,
    connection: MySQLConnection = Depends(get_db),
):
    """Look up a booking using its unguessable booking code; no customer login is required."""
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(
            """SELECT b.booking_code,
                      COALESCE(c.full_name, u.full_name, 'Khách hàng') AS customer_name,
                      c.phone AS customer_phone,
                      r.room_number, rt.name AS room_type,
                      b.check_in, b.check_out, b.guests, b.total_price,
                      b.note, b.status, b.created_at
               FROM bookings b
               LEFT JOIN customers c ON c.id = b.customer_id
               LEFT JOIN users u ON u.id = b.user_id
               JOIN rooms r ON r.id = b.room_id
               JOIN room_types rt ON rt.id = r.room_type_id
               WHERE b.booking_code = %s
               LIMIT 1""",
            (payload.booking_code,),
        )
        booking = cursor.fetchone()
        if not booking:
            raise HTTPException(status_code=404, detail="Không tìm thấy đơn đặt phòng. Vui lòng kiểm tra lại mã.")

        created_at = booking.get("created_at")
        return {
            "booking_code": booking["booking_code"],
            "customer_name": booking["customer_name"],
            "customer_phone_masked": mask_phone_number(booking.get("customer_phone")),
            "room_number": booking["room_number"] if booking["status"] in ("checked_in", "checked_out") else "Xếp khi nhận phòng",
            "room_type": booking["room_type"],
            "check_in": booking["check_in"],
            "check_out": booking["check_out"],
            "guests": booking["guests"],
            "total_price": booking["total_price"],
            "note": booking.get("note"),
            "status": booking["status"],
            "created_at": created_at.isoformat(sep=" ") if created_at else None,
        }
    finally:
        cursor.close()


@app.get("/api/bookings/pending")
def pending_bookings(
    connection: MySQLConnection = Depends(get_db),
    _staff: dict[str, Any] = Depends(require_roles("receptionist", "admin")),
):
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(BOOKING_SELECT_SQL + " WHERE b.status = 'pending' ORDER BY b.created_at ASC")
        return [booking_row_to_output(row) for row in rows_as_dict(cursor)]
    finally:
        cursor.close()


# Tiền dịch vụ được tính khi lễ tân đã tiếp nhận (đang xử lý) hoặc đã hoàn tất, giống quy tắc trên hóa đơn.
SERVICE_TOTAL_SQL = """COALESCE((SELECT SUM(sr.total_price) FROM service_requests sr
                              WHERE sr.booking_id = b.id AND sr.status IN ('in_progress', 'completed')), 0)"""

BOOKING_SELECT_SQL = """SELECT b.id, b.booking_code, b.user_id, b.customer_id,
                      """ + SERVICE_TOTAL_SQL + """ AS service_total,
                      COALESCE(c.full_name, u.full_name) AS customer_name,
                      COALESCE(c.phone, '') AS customer_phone,
                      COALESCE(c.email, u.email) AS customer_email,
                      b.room_id, r.room_number, rt.name AS room_type,
                      b.check_in, b.check_out, b.guests, b.total_price,
                      b.note, b.status, b.created_at,
                      rt.id AS room_type_id, rt.base_price,
                      d.amount AS deposit_amount, d.method AS deposit_method,
                      d.status AS deposit_status, d.received_at AS deposit_received_at
               FROM bookings b
               LEFT JOIN customers c ON c.id = b.customer_id
               LEFT JOIN users u ON u.id = b.user_id
               JOIN rooms r ON r.id = b.room_id
               JOIN room_types rt ON rt.id = r.room_type_id
               LEFT JOIN booking_deposits d ON d.booking_id = b.id"""


@app.get("/api/bookings/active")
def active_bookings(
    connection: MySQLConnection = Depends(get_db),
    _staff: dict[str, Any] = Depends(require_roles("receptionist", "admin")),
):
    """Đơn đã xác nhận (chờ nhận khách) và đơn đang lưu trú (chờ trả phòng)."""
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(
            BOOKING_SELECT_SQL
            + " WHERE b.status IN ('confirmed', 'checked_in') ORDER BY b.check_in ASC"
        )
        return [booking_row_to_output(row) for row in rows_as_dict(cursor)]
    finally:
        cursor.close()


BOOKING_STATUSES = ("pending", "confirmed", "rejected", "cancelled", "checked_in", "checked_out")


@app.get("/api/bookings/history")
def booking_history(
    q: str | None = Query(default=None, max_length=100, description="Tìm theo tên, SĐT, email, mã đơn hoặc số phòng"),
    booking_status: str | None = Query(default=None, alias="status"),
    room_id: int | None = Query(default=None, gt=0),
    date_from: date | None = Query(default=None, description="Lọc đơn có ngày trả phòng từ ngày này"),
    date_to: date | None = Query(default=None, description="Lọc đơn có ngày nhận phòng đến ngày này"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    connection: MySQLConnection = Depends(get_db),
    _staff: dict[str, Any] = Depends(require_roles("receptionist", "admin")),
):
    """Lịch sử toàn bộ đơn đặt phòng, có tìm kiếm, lọc và phân trang."""
    conditions: list[str] = []
    params: list[Any] = []
    if q and q.strip():
        keyword = f"%{q.strip()}%"
        conditions.append(
            """(COALESCE(c.full_name, u.full_name) LIKE %s OR c.phone LIKE %s
                OR COALESCE(c.email, u.email) LIKE %s OR b.booking_code LIKE %s
                OR r.room_number LIKE %s)"""
        )
        params.extend([keyword] * 5)
    if booking_status:
        if booking_status not in BOOKING_STATUSES:
            raise HTTPException(status_code=400, detail="Trạng thái đơn không hợp lệ.")
        conditions.append("b.status = %s")
        params.append(booking_status)
    if room_id:
        conditions.append("b.room_id = %s")
        params.append(room_id)
    if date_from:
        conditions.append("b.check_out >= %s")
        params.append(date_from)
    if date_to:
        conditions.append("b.check_in <= %s")
        params.append(date_to)
    where_sql = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(
            """SELECT COUNT(*) AS total
               FROM bookings b
               LEFT JOIN customers c ON c.id = b.customer_id
               LEFT JOIN users u ON u.id = b.user_id
               JOIN rooms r ON r.id = b.room_id""" + where_sql,
            tuple(params),
        )
        total = cursor.fetchone()["total"]
        cursor.execute(
            BOOKING_SELECT_SQL + where_sql + " ORDER BY b.created_at DESC, b.id DESC LIMIT %s OFFSET %s",
            tuple(params) + (page_size, (page - 1) * page_size),
        )
        items = [booking_row_to_output(row) for row in rows_as_dict(cursor)]
        return {"items": items, "total": total, "page": page, "page_size": page_size}
    finally:
        cursor.close()


@app.get("/api/staff/rooms/{room_id}/detail")
def staff_room_detail(
    room_id: int,
    connection: MySQLConnection = Depends(get_db),
    _staff: dict[str, Any] = Depends(require_roles("receptionist", "admin")),
):
    """Thông tin một phòng: khách đang ở, các đơn sắp tới và lịch sử khách đã ở."""
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(
            """SELECT r.id, r.room_number, r.floor, r.operational_status,
                      rt.name AS room_type, rt.base_price AS price_per_night, rt.max_guests
               FROM rooms r JOIN room_types rt ON rt.id = r.room_type_id
               WHERE r.id = %s AND r.is_active = TRUE""",
            (room_id,),
        )
        room = cursor.fetchone()
        if not room:
            raise HTTPException(status_code=404, detail="Không tìm thấy phòng.")

        cursor.execute(
            BOOKING_SELECT_SQL + " WHERE b.room_id = %s ORDER BY b.check_in DESC, b.id DESC LIMIT 200",
            (room_id,),
        )
        bookings = [booking_row_to_output(row) for row in rows_as_dict(cursor)]

        current = next((b for b in bookings if b["status"] == "checked_in"), None)
        if current and room["operational_status"] == "available":
            room["operational_status"] = "occupied"
        upcoming = sorted(
            (b for b in bookings if b["status"] in ("pending", "confirmed")),
            key=lambda b: b["check_in"],
        )
        history = [b for b in bookings if b["status"] in ("checked_out", "cancelled", "rejected")]
        current_services: list[dict[str, Any]] = []
        if current:
            cursor.execute(
                SERVICE_SELECT_SQL + " WHERE s.booking_id = %s ORDER BY s.created_at DESC",
                (current["id"],),
            )
            current_services = [service_row_to_output(row) for row in rows_as_dict(cursor)]
        return {
            "current_services": current_services,
            "current_services_total": sum(
                (Decimal(sv["total_price"]) for sv in current_services if sv["status"] != "cancelled"),
                Decimal("0"),
            ),
            "room": room,
            "current_booking": current,
            "upcoming_bookings": upcoming,
            "history": history,
            "total_stays": sum(1 for b in bookings if b["status"] == "checked_out"),
        }
    finally:
        cursor.close()


@app.patch("/api/bookings/{booking_id}/status")
def update_booking_status(
    booking_id: int,
    payload: BookingStatusInput,
    connection: MySQLConnection = Depends(get_db),
    staff: dict[str, Any] = Depends(require_roles("receptionist", "admin")),
):
    # Các bước chuyển trạng thái hợp lệ của đơn.
    # pending -> checked_in được phép để lễ tân nhận khách trực tiếp tại quầy.
    allowed_next = {
        "pending": {"confirmed", "rejected", "checked_in"},
        "confirmed": {"checked_in"},
        "checked_in": {"checked_out"},
    }
    # Hủy đơn dùng API riêng POST /api/bookings/{id}/cancel để xử lý tiền cọc và lý do hủy.
    # Trạng thái phòng tương ứng sau khi đổi trạng thái đơn.
    room_status_map = {
        "checked_in": "occupied",   # Nhận khách -> phòng đang có khách
        "checked_out": "cleaning",  # Trả phòng -> phòng chờ dọn
    }
    cursor = connection.cursor(dictionary=True)
    try:
        # get_current_user đã chạy một SELECT trên connection này; kết thúc transaction đó trước.
        connection.rollback()
        connection.start_transaction()
        cursor.execute(
            "SELECT id, status, room_id, check_in, check_out FROM bookings WHERE id = %s FOR UPDATE",
            (booking_id,),
        )
        booking = cursor.fetchone()
        if not booking:
            connection.rollback()
            raise HTTPException(status_code=404, detail="Không tìm thấy đơn đặt phòng.")
        if payload.status not in allowed_next.get(booking["status"], set()):
            connection.rollback()
            raise HTTPException(
                status_code=409,
                detail=f"Không thể chuyển đơn từ trạng thái {booking['status']} sang {payload.status}.",
            )
        if payload.status == "checked_in":
            today = date.today()
            if today < booking["check_in"]:
                raise HTTPException(
                    status_code=409,
                    detail=f"Chưa đến ngày nhận phòng. Đơn này nhận phòng ngày {booking['check_in']:%d/%m/%Y}. "
                           "Nếu khách đến sớm, hãy sửa ngày nhận phòng trước.",
                )
            if today >= booking["check_out"]:
                raise HTTPException(
                    status_code=409,
                    detail="Đã qua ngày trả phòng của đơn này. Hãy sửa ngày lưu trú hoặc hủy đơn.",
                )
            cursor.execute(
                "SELECT id FROM bookings WHERE room_id = %s AND status = 'checked_in' AND id <> %s LIMIT 1",
                (booking["room_id"], booking_id),
            )
            if cursor.fetchone():
                raise HTTPException(
                    status_code=409,
                    detail="Phòng này vẫn còn khách chưa trả phòng. Hãy trả phòng cho khách trước hoặc đổi phòng cho đơn này.",
                )

        cursor.execute(
            """UPDATE bookings SET status = %s, reviewed_by = %s, reviewed_at = %s
               WHERE id = %s""",
            (payload.status, staff["id"], datetime.now(), booking_id),
        )

        status_log = {"confirmed": "Xác nhận đơn", "rejected": "Từ chối đơn",
                      "checked_in": "Nhận khách", "checked_out": "Trả phòng và xuất hóa đơn"}
        log_booking_change(cursor, booking_id, payload.status, status_log.get(payload.status, payload.status), staff["id"])

        # Trả phòng: chốt dịch vụ và xuất hóa đơn trong cùng transaction.
        if payload.status == "checked_out":
            finalize_checkout_invoice(cursor, booking_id, staff["id"])

        # Đồng bộ trạng thái phòng trong cùng transaction với đơn.
        new_room_status = room_status_map.get(payload.status)
        if new_room_status:
            cursor.execute(
                "UPDATE rooms SET operational_status = %s WHERE id = %s",
                (new_room_status, booking["room_id"]),
            )

        connection.commit()

        cursor.execute(BOOKING_SELECT_SQL + " WHERE b.id = %s", (booking_id,))
        return booking_row_to_output(cursor.fetchone())
    except HTTPException:
        connection.rollback()
        raise
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


@app.patch("/api/rooms/{room_id}/operational-status")
def update_room_status(
    room_id: int,
    operational_status: str = Query(pattern="^(available|occupied|cleaning|maintenance)$"),
    connection: MySQLConnection = Depends(get_db),
    _staff: dict[str, Any] = Depends(require_roles("receptionist", "admin")),
):
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT id FROM rooms WHERE id = %s AND is_active = TRUE", (room_id,))
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy phòng.")
        if operational_status == "occupied":
            # Phòng chỉ được đánh dấu "đang có khách" khi có lượt lưu trú kèm thông tin khách,
            # nếu không hệ thống không biết ngày trả phòng và vẫn bán phòng cho khách khác.
            cursor.execute(
                "SELECT id FROM bookings WHERE room_id = %s AND status = 'checked_in' LIMIT 1",
                (room_id,),
            )
            if cursor.fetchone() is None:
                raise HTTPException(
                    status_code=409,
                    detail="Hãy dùng Thêm khách tại quầy để nhập thông tin khách và ngày trả phòng.",
                )
        cursor.execute("UPDATE rooms SET operational_status = %s WHERE id = %s", (operational_status, room_id))
        connection.commit()
        return {"room_id": room_id, "operational_status": operational_status}
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


@app.post("/api/staff/walk-ins", status_code=status.HTTP_201_CREATED)
def create_walk_in(
    payload: WalkInInput,
    connection: MySQLConnection = Depends(get_db),
    staff: dict[str, Any] = Depends(require_roles("receptionist", "admin")),
):
    """Lễ tân thêm khách đến thẳng quầy: tạo lượt lưu trú đang ở và khóa phòng lại."""
    today = date.today()
    if payload.check_out <= today:
        raise HTTPException(status_code=400, detail="Ngày trả phòng phải sau ngày hôm nay.")
    if (payload.check_out - today).days > 365:
        raise HTTPException(status_code=400, detail="Lượt lưu trú tối đa 365 đêm.")

    cursor = connection.cursor(dictionary=True)
    try:
        connection.rollback()
        connection.start_transaction()
        cursor.execute(
            """SELECT r.id, r.room_number, r.operational_status,
                      rt.name AS room_type, rt.base_price, rt.max_guests
               FROM rooms r JOIN room_types rt ON rt.id = r.room_type_id
               WHERE r.id = %s AND r.is_active = TRUE AND rt.is_active = TRUE
               FOR UPDATE""",
            (payload.room_id,),
        )
        room = cursor.fetchone()
        if not room:
            raise HTTPException(status_code=404, detail="Không tìm thấy phòng.")
        if room["operational_status"] == "maintenance":
            raise HTTPException(status_code=409, detail="Phòng đang bảo trì, không thể nhận khách.")
        if payload.guests > room["max_guests"]:
            raise HTTPException(status_code=400, detail=f"Phòng chỉ phục vụ tối đa {room['max_guests']} khách.")

        cursor.execute(
            "SELECT id FROM bookings WHERE room_id = %s AND status = 'checked_in' LIMIT 1",
            (payload.room_id,),
        )
        if cursor.fetchone():
            raise HTTPException(
                status_code=409,
                detail="Phòng này vẫn còn khách chưa trả phòng. Hãy trả phòng cho khách trước.",
            )
        cursor.execute(
            """SELECT booking_code, check_in, check_out FROM bookings
               WHERE room_id = %s AND status IN ('pending', 'confirmed')
                 AND check_in < %s AND check_out > %s
               ORDER BY check_in LIMIT 1 FOR UPDATE""",
            (payload.room_id, payload.check_out, today),
        )
        clash = cursor.fetchone()
        if clash:
            raise HTTPException(
                status_code=409,
                detail=f"Phòng đã có đơn {clash['booking_code']} từ {clash['check_in']:%d/%m/%Y} "
                       f"đến {clash['check_out']:%d/%m/%Y}. Hãy chọn phòng khác hoặc rút ngắn ngày trả phòng.",
            )

        nights = (payload.check_out - today).days
        total_price = room["base_price"] * nights
        booking_code = f"GV-{secrets.token_hex(16).upper()}"
        cursor.execute(
            "INSERT INTO customers (full_name, phone, email) VALUES (%s, %s, %s)",
            (payload.customer_name, payload.customer_phone, payload.customer_email),
        )
        customer_id = cursor.lastrowid
        cursor.execute(
            """INSERT INTO bookings
               (booking_code, user_id, customer_id, room_id, check_in, check_out, guests,
                total_price, note, status, reviewed_by, reviewed_at)
               VALUES (%s, NULL, %s, %s, %s, %s, %s, %s, %s, 'checked_in', %s, %s)""",
            (booking_code, customer_id, payload.room_id, today, payload.check_out,
             payload.guests, total_price, payload.note, staff["id"], datetime.now()),
        )
        booking_id = cursor.lastrowid
        log_booking_change(
            cursor, booking_id, "checked_in",
            f"Thêm khách tại quầy, phòng {room['room_number']}, trả phòng {payload.check_out:%d/%m/%Y}",
            staff["id"],
        )
        cursor.execute("UPDATE rooms SET operational_status = 'occupied' WHERE id = %s", (payload.room_id,))
        connection.commit()
        return {
            "id": booking_id,
            "booking_code": booking_code,
            "room_id": payload.room_id,
            "room_number": room["room_number"],
            "room_type": room["room_type"],
            "customer_name": payload.customer_name,
            "customer_phone": payload.customer_phone,
            "customer_email": payload.customer_email,
            "check_in": today,
            "check_out": payload.check_out,
            "guests": payload.guests,
            "nights": nights,
            "total_price": total_price,
            "note": payload.note,
            "status": "checked_in",
        }
    except HTTPException:
        connection.rollback()
        raise
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


@app.get("/api/dashboard/summary")
def dashboard_summary(
    connection: MySQLConnection = Depends(get_db),
    _admin: dict[str, Any] = Depends(require_roles("admin")),
):
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("SELECT COUNT(*) AS total_rooms FROM rooms WHERE is_active = TRUE")
        total_rooms = cursor.fetchone()["total_rooms"]
        cursor.execute(
            """SELECT COUNT(DISTINCT room_id) AS occupied_rooms
               FROM bookings WHERE status IN ('confirmed', 'checked_in')
                 AND check_in <= CURRENT_DATE() AND check_out > CURRENT_DATE()"""
        )
        occupied_rooms = cursor.fetchone()["occupied_rooms"]
        cursor.execute("SELECT COUNT(*) AS pending_bookings FROM bookings WHERE status = 'pending'")
        pending = cursor.fetchone()["pending_bookings"]
        cursor.execute(
            "SELECT COUNT(*) AS bookings_today FROM bookings WHERE DATE(created_at) = CURRENT_DATE()"
        )
        today = cursor.fetchone()["bookings_today"]
        cursor.execute(
            """SELECT
                   (SELECT COALESCE(SUM(total_price), 0) FROM bookings WHERE status = 'checked_out')
                 + (SELECT COALESCE(SUM(service_amount), 0) FROM invoices) AS completed_revenue"""
        )
        revenue = cursor.fetchone()["completed_revenue"]
        occupancy_rate = round(occupied_rooms / total_rooms * 100, 1) if total_rooms else 0
        return {
            "total_rooms": total_rooms,
            "occupied_rooms": occupied_rooms,
            "occupancy_rate_percent": occupancy_rate,
            "pending_bookings": pending,
            "bookings_created_today": today,
            "completed_revenue": revenue,
        }
    finally:
        cursor.close()


# ==========================================================
# Dịch vụ phòng: ăn uống, đặt xe, dọn phòng, spa
# ==========================================================
# Bảng giá đặt ở phía máy chủ để khách không thể tự sửa giá từ trình duyệt.
# Mọi dịch vụ đều cho phép để trống thời gian: để trống nghĩa là cần ngay.
SERVICE_CATALOG: dict[str, dict[str, Any]] = {
    "dining": {
        "label": "Ăn uống",
        "description": "Phục vụ tận phòng từ 6:00 đến 23:00, thường mất 25 đến 35 phút.",
        "needs_destination": False,
        "single_item": False,
        "items": [
            {"id": "pho-bo", "name": "Phở bò tái", "detail": "Nước dùng hầm xương, bánh phở tươi", "price": 95000},
            {"id": "com-rang", "name": "Cơm rang hải sản", "detail": "Tôm, mực, trứng và rau theo mùa", "price": 120000},
            {"id": "banh-mi-chao", "name": "Bánh mì chảo", "detail": "Trứng ốp, pate, xúc xích, bánh mì nóng", "price": 85000},
            {"id": "salad", "name": "Salad rau vườn", "detail": "Rau hữu cơ, sốt chanh leo", "price": 75000},
            {"id": "trai-cay", "name": "Đĩa trái cây theo mùa", "detail": "Cho 2 người", "price": 65000},
            {"id": "ca-phe", "name": "Cà phê sữa đá", "detail": "Cà phê phin", "price": 45000},
            {"id": "nuoc-cam", "name": "Nước cam ép", "detail": "Cam tươi, không thêm đường", "price": 55000},
            {"id": "nuoc-suoi", "name": "Nước suối", "detail": "Chai 500 ml", "price": 20000},
        ],
    },
    "transport": {
        "label": "Đặt xe",
        "description": "Để trống thời gian nếu cần xe ngay. Lễ tân gọi lại báo tài xế và biển số xe.",
        "needs_destination": True,
        "single_item": True,
        "items": [
            {"id": "san-bay-4", "name": "Đưa ra sân bay, xe 4 chỗ", "detail": "Tối đa 3 khách, 2 vali", "price": 350000},
            {"id": "san-bay-7", "name": "Đưa ra sân bay, xe 7 chỗ", "detail": "Tối đa 6 khách", "price": 450000},
            {"id": "tham-quan", "name": "Xe tham quan nửa ngày", "detail": "4 giờ, xe 4 chỗ kèm tài xế", "price": 900000},
            {"id": "taxi", "name": "Gọi taxi", "detail": "Trả trực tiếp cho tài xế", "price": 0, "price_label": "Theo đồng hồ"},
        ],
    },
    "housekeeping": {
        "label": "Dọn phòng và giặt là",
        "description": "Đồ giặt được trả lại trong ngày nếu gửi trước 10:00.",
        "needs_destination": False,
        "single_item": False,
        "items": [
            {"id": "don-phong", "name": "Dọn phòng", "detail": "Thay ga, lau dọn, đổ rác", "price": 0},
            {"id": "khan-tam", "name": "Thêm khăn tắm", "detail": "Mỗi lượt 2 khăn", "price": 0},
            {"id": "goi", "name": "Thêm gối", "detail": "", "price": 0},
            {"id": "giat-say", "name": "Giặt sấy", "detail": "Tính theo kg", "price": 60000},
            {"id": "la-ui", "name": "Là ủi", "detail": "Tính theo món", "price": 30000},
        ],
    },
    "spa": {
        "label": "Spa",
        "description": "Mở cửa từ 9:00 đến 21:00. Để trống thời gian nếu muốn vào ngay khi có chỗ.",
        "needs_destination": False,
        "single_item": False,
        "items": [
            {"id": "massage-chan", "name": "Massage chân 60 phút", "detail": "Thảo dược ngâm chân", "price": 450000},
            {"id": "massage-body", "name": "Massage toàn thân 90 phút", "detail": "Tinh dầu sả chanh", "price": 850000},
            {"id": "xong-hoi", "name": "Xông hơi thảo dược 45 phút", "detail": "", "price": 300000},
        ],
    },
}
SERVICE_STATUSES = ("pending", "in_progress", "completed", "cancelled")

# Cột cấu trúc phòng, tự thêm khi khởi động nếu cơ sở dữ liệu cũ chưa có (migration 005).
ROOM_TYPE_DETAIL_COLUMNS_SQL = [
    ("area_sqm", "SMALLINT UNSIGNED NULL"),
    ("bed_type", "VARCHAR(80) NULL"),
    ("view_name", "VARCHAR(80) NULL"),
    ("layout_summary", "VARCHAR(255) NULL"),
    ("amenities", "VARCHAR(600) NULL"),
    ("gallery_json", "TEXT NULL"),
]

SERVICE_TABLE_SQL = """CREATE TABLE IF NOT EXISTS service_requests (
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""

INVOICE_TABLE_SQL = """CREATE TABLE IF NOT EXISTS invoices (
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""

DEPOSIT_TABLE_SQL = """CREATE TABLE IF NOT EXISTS booking_deposits (
    booking_id INT NOT NULL PRIMARY KEY,
    amount DECIMAL(12, 2) NOT NULL,
    method VARCHAR(20) NOT NULL,
    note VARCHAR(255) NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'paid',
    received_by INT NULL,
    received_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    settled_at DATETIME NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""

CHANGE_TABLE_SQL = """CREATE TABLE IF NOT EXISTS booking_changes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    booking_id INT NOT NULL,
    action VARCHAR(30) NOT NULL,
    detail VARCHAR(1000) NOT NULL,
    staff_id INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_change_booking (booking_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""

SERVICE_SELECT_SQL = """SELECT s.id, s.booking_id, s.room_id, s.category, s.items_json,
                      s.scheduled_at, s.destination, s.note, s.total_price, s.status,
                      s.created_at, s.updated_at,
                      r.room_number, b.booking_code,
                      COALESCE(c.full_name, u.full_name) AS customer_name,
                      c.phone AS customer_phone
               FROM service_requests s
               JOIN bookings b ON b.id = s.booking_id
               JOIN rooms r ON r.id = s.room_id
               LEFT JOIN customers c ON c.id = b.customer_id
               LEFT JOIN users u ON u.id = b.user_id"""

GUEST_BOOKING_SQL = """SELECT b.id, b.booking_code, b.room_id, b.status, b.check_in, b.check_out, b.guests,
                  COALESCE(c.full_name, u.full_name, 'Quý khách') AS customer_name,
                  c.phone AS customer_phone,
                  r.room_number, rt.name AS room_type
           FROM bookings b
           LEFT JOIN customers c ON c.id = b.customer_id
           LEFT JOIN users u ON u.id = b.user_id
           JOIN rooms r ON r.id = b.room_id
           JOIN room_types rt ON rt.id = r.room_type_id"""


@app.on_event("startup")
def ensure_extra_tables() -> None:
    """Tự thêm cột cấu trúc phòng và các bảng dịch vụ, hóa đơn nếu chưa có (migration 002 đến 005)."""
    try:
        connection = connect_db()
        cursor = connection.cursor()
        for column, definition in ROOM_TYPE_DETAIL_COLUMNS_SQL:
            cursor.execute(
                """SELECT COUNT(*) FROM information_schema.columns
                   WHERE table_schema = DATABASE() AND table_name = 'room_types' AND column_name = %s""",
                (column,),
            )
            if cursor.fetchone()[0] == 0:
                cursor.execute(f"ALTER TABLE room_types ADD COLUMN {column} {definition}")
        cursor.execute(SERVICE_TABLE_SQL)
        cursor.execute(INVOICE_TABLE_SQL)
        cursor.execute(DEPOSIT_TABLE_SQL)
        cursor.execute(CHANGE_TABLE_SQL)
        connection.commit()
        cursor.close()
        connection.close()
    except mysql.connector.Error as exc:
        print(f"[CẢNH BÁO] Chưa tạo được bảng dịch vụ/hóa đơn: {exc}. "
              "Hãy chạy các file trong database/migrations (002 đến 005).")


def money_value(value: Any) -> int | float:
    """Chuyển Decimal sang số để lưu JSON hóa đơn."""
    number = Decimal(str(value or 0))
    return int(number) if number == number.to_integral_value() else float(number)


def format_dt(value: Any) -> str | None:
    return value.isoformat(sep=" ", timespec="minutes") if value else None


def service_row_to_output(row: dict[str, Any], include_staff_fields: bool = True) -> dict[str, Any]:
    category = SERVICE_CATALOG.get(row["category"], {})
    try:
        items = json.loads(row["items_json"])
    except (TypeError, ValueError):
        items = []
    output = {
        "id": row["id"],
        "category": row["category"],
        "category_label": category.get("label", row["category"]),
        "items": items,
        "scheduled_at": format_dt(row.get("scheduled_at")),
        "destination": row.get("destination"),
        "note": row.get("note"),
        "total_price": row["total_price"],
        "status": row["status"],
        "room_number": row.get("room_number"),
        "created_at": format_dt(row.get("created_at")),
        "updated_at": format_dt(row.get("updated_at")),
    }
    if include_staff_fields:
        output.update({
            "booking_id": row.get("booking_id"),
            "booking_code": row.get("booking_code"),
            "customer_name": row.get("customer_name"),
            "customer_phone": row.get("customer_phone"),
        })
    return output


# ---------- Vào dịch vụ phòng bằng số điện thoại ----------
def normalize_phone(value: str | None) -> str:
    """Chỉ giữ chữ số; đổi đầu số +84/84 thành 0 để 0901234567 và +84 901 234 567 khớp nhau."""
    digits = "".join(character for character in str(value or "") if character.isdigit())
    if digits.startswith("84") and len(digits) >= 11:
        digits = "0" + digits[2:]
    return digits


# Giới hạn thử số điện thoại theo địa chỉ IP để tránh dò số (lưu trong bộ nhớ, đủ cho đồ án).
_PHONE_ATTEMPTS: dict[str, list[datetime]] = {}
PHONE_ATTEMPT_LIMIT = 10
PHONE_ATTEMPT_WINDOW = timedelta(minutes=10)


def check_phone_rate_limit(client_ip: str) -> None:
    now = datetime.now()
    attempts = [moment for moment in _PHONE_ATTEMPTS.get(client_ip, []) if now - moment < PHONE_ATTEMPT_WINDOW]
    if len(attempts) >= PHONE_ATTEMPT_LIMIT:
        _PHONE_ATTEMPTS[client_ip] = attempts
        raise HTTPException(status_code=429, detail="Bạn đã thử quá nhiều lần. Vui lòng đợi 10 phút hoặc liên hệ lễ tân.")
    attempts.append(now)
    _PHONE_ATTEMPTS[client_ip] = attempts


def create_guest_token(booking_id: int) -> str:
    if not config.JWT_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Chưa cấu hình JWT_SECRET_KEY trong file .env.")
    payload = {
        # Tiền tố "guest:" khiến token này không bao giờ dùng được như token nhân viên.
        "sub": f"guest:{booking_id}",
        "typ": "guest",
        "exp": datetime.now(timezone.utc) + timedelta(hours=24),
    }
    return jwt.encode(payload, config.JWT_SECRET_KEY, algorithm=config.JWT_ALGORITHM)


guest_bearer = HTTPBearer(auto_error=False)


def get_guest_booking(
    credentials: HTTPAuthorizationCredentials | None = Depends(guest_bearer),
    connection: MySQLConnection = Depends(get_db),
) -> dict[str, Any]:
    expired = HTTPException(status_code=401, detail="Phiên dịch vụ phòng đã hết hạn. Vui lòng nhập lại số điện thoại.")
    if credentials is None or not config.JWT_SECRET_KEY:
        raise expired
    try:
        payload = jwt.decode(credentials.credentials, config.JWT_SECRET_KEY, algorithms=[config.JWT_ALGORITHM])
        if payload.get("typ") != "guest":
            raise expired
        booking_id = int(str(payload.get("sub", "")).removeprefix("guest:"))
    except (jwt.InvalidTokenError, TypeError, ValueError):
        raise expired
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(GUEST_BOOKING_SQL + " WHERE b.id = %s LIMIT 1", (booking_id,))
        booking = cursor.fetchone()
    finally:
        cursor.close()
    if not booking:
        raise expired
    if booking["status"] != "checked_in":
        raise HTTPException(status_code=403, detail="Lượt lưu trú đã kết thúc hoặc chưa nhận phòng nên dịch vụ phòng đã đóng.")
    return booking


def stay_summary(booking: dict[str, Any], with_token: bool = False) -> dict[str, Any]:
    summary = {
        "customer_name": booking["customer_name"],
        "room_number": booking["room_number"],
        "room_type": booking["room_type"],
        "check_in": booking["check_in"],
        "check_out": booking["check_out"],
        "guests": booking["guests"],
    }
    if with_token:
        summary["token"] = create_guest_token(booking["id"])
    return summary


class GuestEntryInput(BaseModel):
    phone: str | None = Field(default=None, max_length=30)
    booking_code: str | None = Field(default=None, max_length=40)


class ServiceItemInput(BaseModel):
    item_id: str = Field(min_length=1, max_length=40)
    quantity: int = Field(ge=1, le=20)


class ServiceRequestInput(BaseModel):
    category: Literal["dining", "transport", "housekeeping", "spa"]
    items: list[ServiceItemInput] = Field(min_length=1, max_length=20)
    scheduled_at: datetime | None = None
    destination: str | None = Field(default=None, max_length=255)
    note: str | None = Field(default=None, max_length=500)


class ServiceStatusInput(BaseModel):
    status: Literal["in_progress", "completed", "cancelled"]


@app.get("/api/services/catalog")
def service_catalog():
    """Danh mục dịch vụ công khai cho trang dịch vụ phòng."""
    return [{"id": key, **value} for key, value in SERVICE_CATALOG.items()]


@app.post("/api/guest/service-session")
def guest_service_session(
    payload: GuestEntryInput,
    request: Request,
    connection: MySQLConnection = Depends(get_db),
):
    """Khách nhập số điện thoại đã dùng khi đặt phòng (hoặc mã đặt phòng) để vào dịch vụ phòng.

    Trả về danh sách lượt lưu trú đang diễn ra kèm token riêng cho từng phòng.
    Một số điện thoại có thể đang ở nhiều phòng, khi đó khách chọn phòng.
    """
    cursor = connection.cursor(dictionary=True)
    try:
        if payload.booking_code:
            code = payload.booking_code.strip().upper()
            cursor.execute(GUEST_BOOKING_SQL + " WHERE b.booking_code = %s LIMIT 1", (code,))
            booking = cursor.fetchone()
            if not booking:
                raise HTTPException(status_code=404, detail="Không tìm thấy đơn đặt phòng.")
            if booking["status"] != "checked_in":
                raise HTTPException(status_code=403, detail="Dịch vụ phòng chỉ mở khi bạn đang lưu trú.")
            return {"stays": [stay_summary(booking, with_token=True)]}

        phone = normalize_phone(payload.phone)
        if not 8 <= len(phone) <= 15:
            raise HTTPException(status_code=400, detail="Vui lòng nhập số điện thoại hợp lệ.")
        check_phone_rate_limit(request.client.host if request.client else "unknown")

        cursor.execute(
            GUEST_BOOKING_SQL + " WHERE b.status IN ('checked_in', 'confirmed', 'pending') AND c.phone IS NOT NULL"
        )
        matches = [row for row in rows_as_dict(cursor) if normalize_phone(row["customer_phone"]) == phone]
        staying = [row for row in matches if row["status"] == "checked_in"]
        if staying:
            staying.sort(key=lambda row: row["room_number"])
            return {"stays": [stay_summary(row, with_token=True) for row in staying]}
        if matches:
            raise HTTPException(
                status_code=403,
                detail="Bạn chưa nhận phòng. Dịch vụ phòng mở sau khi lễ tân làm thủ tục nhận phòng.",
            )
        raise HTTPException(
            status_code=404,
            detail="Không tìm thấy lượt lưu trú nào với số điện thoại này. Hãy nhập đúng số đã dùng khi đặt phòng.",
        )
    finally:
        cursor.close()


class PhoneLookupInput(BaseModel):
    phone: str = Field(min_length=8, max_length=30)


@app.post("/api/bookings/lookup-by-phone")
def lookup_bookings_by_phone(
    payload: PhoneLookupInput,
    request: Request,
    connection: MySQLConnection = Depends(get_db),
):
    """Khách tra cứu đơn bằng số điện thoại người đặt; trả về mã đặt phòng và tổng tiền (gồm dịch vụ)."""
    phone = normalize_phone(payload.phone)
    if not 8 <= len(phone) <= 15:
        raise HTTPException(status_code=400, detail="Vui lòng nhập số điện thoại hợp lệ.")
    check_phone_rate_limit(request.client.host if request.client else "unknown")

    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(
            """SELECT b.booking_code, b.status, b.check_in, b.check_out, b.guests,
                      b.total_price, b.note, b.created_at,
                      """ + SERVICE_TOTAL_SQL + """ AS service_total,
                      COALESCE(c.full_name, 'Khách hàng') AS customer_name, c.phone AS customer_phone,
                      r.room_number, rt.name AS room_type,
                      d.amount AS deposit_amount, d.status AS deposit_status
               FROM bookings b
               JOIN customers c ON c.id = b.customer_id
               JOIN rooms r ON r.id = b.room_id
               JOIN room_types rt ON rt.id = r.room_type_id
               LEFT JOIN booking_deposits d ON d.booking_id = b.id
               WHERE c.phone IS NOT NULL
               ORDER BY b.created_at DESC, b.id DESC"""
        )
        matches = [row for row in rows_as_dict(cursor) if normalize_phone(row["customer_phone"]) == phone][:10]
        if not matches:
            raise HTTPException(
                status_code=404,
                detail="Không tìm thấy đơn nào với số điện thoại này. Hãy nhập đúng số đã dùng khi đặt phòng.",
            )
        # Đơn đang lưu trú và sắp tới lên đầu, đơn đã kết thúc xuống sau.
        order = {"checked_in": 0, "confirmed": 1, "pending": 2, "checked_out": 3, "cancelled": 4, "rejected": 5}
        matches.sort(key=lambda row: order.get(row["status"], 9))
        results = []
        for row in matches:
            room_total = Decimal(str(row["total_price"]))
            service_total = Decimal(str(row["service_total"] or 0))
            deposit = Decimal(str(row["deposit_amount"])) if row["deposit_status"] in ("paid", "applied") else Decimal("0")
            results.append({
                "booking_code": row["booking_code"],
                "customer_name": row["customer_name"],
                "customer_phone_masked": mask_phone_number(row["customer_phone"]),
                # Số phòng chỉ cho khách biết khi đã nhận phòng
                "room_number": row["room_number"] if row["status"] in ("checked_in", "checked_out") else None,
                "deposit_paid": deposit,
                "deposit_status": row["deposit_status"],
                "amount_due": room_total + service_total - deposit,
                "room_type": row["room_type"],
                "check_in": row["check_in"],
                "check_out": row["check_out"],
                "nights": max(1, (row["check_out"] - row["check_in"]).days),
                "guests": row["guests"],
                "room_total": room_total,
                "service_total": service_total,
                "grand_total": room_total + service_total,
                "note": row.get("note"),
                "status": row["status"],
                "created_at": format_dt(row.get("created_at")),
            })
        return results
    finally:
        cursor.close()


@app.get("/api/guest/me")
def guest_me(booking: dict[str, Any] = Depends(get_guest_booking)):
    return stay_summary(booking)


@app.post("/api/guest/service-requests", status_code=status.HTTP_201_CREATED)
def create_service_request(
    payload: ServiceRequestInput,
    guest: dict[str, Any] = Depends(get_guest_booking),
    connection: MySQLConnection = Depends(get_db),
):
    category = SERVICE_CATALOG[payload.category]
    catalog_items = {item["id"]: item for item in category["items"]}

    merged: dict[str, int] = {}
    for line in payload.items:
        if line.item_id not in catalog_items:
            raise HTTPException(status_code=400, detail="Có dịch vụ không còn trong danh mục. Vui lòng tải lại trang.")
        merged[line.item_id] = min(20, merged.get(line.item_id, 0) + line.quantity)
    if category["single_item"] and (len(merged) != 1 or next(iter(merged.values())) != 1):
        raise HTTPException(status_code=400, detail="Mỗi yêu cầu đặt xe chỉ chọn một chuyến.")

    # Thời gian không bắt buộc: để trống nghĩa là khách cần ngay.
    scheduled_at = payload.scheduled_at.replace(tzinfo=None) if payload.scheduled_at else None
    if scheduled_at:
        now = datetime.now()
        if scheduled_at < now - timedelta(minutes=5):
            raise HTTPException(status_code=400, detail="Thời gian mong muốn không thể ở trong quá khứ.")
        if scheduled_at > now + timedelta(days=30):
            raise HTTPException(status_code=400, detail="Chỉ đặt trước tối đa 30 ngày.")
    destination = (payload.destination or "").strip() or None
    if category["needs_destination"] and not destination:
        raise HTTPException(status_code=400, detail="Vui lòng nhập điểm đến.")
    note = (payload.note or "").strip() or None

    lines = [
        {
            "item_id": item_id,
            "name": catalog_items[item_id]["name"],
            "quantity": quantity,
            "unit_price": catalog_items[item_id]["price"],
        }
        for item_id, quantity in merged.items()
    ]
    total_price = sum(line["unit_price"] * line["quantity"] for line in lines)

    cursor = connection.cursor(dictionary=True)
    try:
        connection.rollback()
        connection.start_transaction()
        cursor.execute("SELECT status FROM bookings WHERE id = %s FOR UPDATE", (guest["id"],))
        locked = cursor.fetchone()
        if not locked or locked["status"] != "checked_in":
            raise HTTPException(status_code=403, detail="Lượt lưu trú đã kết thúc nên không thể gọi thêm dịch vụ.")
        cursor.execute(
            "SELECT COUNT(*) AS open_count FROM service_requests WHERE booking_id = %s AND status = 'pending'",
            (guest["id"],),
        )
        if cursor.fetchone()["open_count"] >= 10:
            raise HTTPException(status_code=429, detail="Bạn đang có nhiều yêu cầu chờ xử lý. Vui lòng đợi lễ tân tiếp nhận.")
        cursor.execute(
            """INSERT INTO service_requests
               (booking_id, room_id, category, items_json, scheduled_at, destination, note, total_price, status)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending')""",
            (
                guest["id"], guest["room_id"], payload.category,
                json.dumps(lines, ensure_ascii=False), scheduled_at,
                destination if category["needs_destination"] else None, note, total_price,
            ),
        )
        request_id = cursor.lastrowid
        connection.commit()
        cursor.execute(SERVICE_SELECT_SQL + " WHERE s.id = %s", (request_id,))
        return service_row_to_output(cursor.fetchone(), include_staff_fields=False)
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


@app.get("/api/guest/service-requests")
def list_guest_service_requests(
    guest: dict[str, Any] = Depends(get_guest_booking),
    connection: MySQLConnection = Depends(get_db),
):
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(
            SERVICE_SELECT_SQL + " WHERE s.booking_id = %s ORDER BY s.created_at DESC, s.id DESC",
            (guest["id"],),
        )
        return [service_row_to_output(row, include_staff_fields=False) for row in rows_as_dict(cursor)]
    finally:
        cursor.close()


@app.post("/api/guest/service-requests/{request_id}/cancel")
def cancel_guest_service_request(
    request_id: int,
    guest: dict[str, Any] = Depends(get_guest_booking),
    connection: MySQLConnection = Depends(get_db),
):
    cursor = connection.cursor(dictionary=True)
    try:
        connection.rollback()
        connection.start_transaction()
        cursor.execute(
            "SELECT id, status FROM service_requests WHERE id = %s AND booking_id = %s FOR UPDATE",
            (request_id, guest["id"]),
        )
        service = cursor.fetchone()
        if not service:
            raise HTTPException(status_code=404, detail="Không tìm thấy yêu cầu dịch vụ.")
        if service["status"] != "pending":
            raise HTTPException(status_code=409, detail="Yêu cầu đã được tiếp nhận nên không thể tự hủy. Vui lòng gọi lễ tân.")
        cursor.execute("UPDATE service_requests SET status = 'cancelled' WHERE id = %s", (request_id,))
        connection.commit()
        return {"id": request_id, "status": "cancelled"}
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


@app.get("/api/service-requests")
def staff_service_requests(
    request_status: str | None = Query(default="open", alias="status"),
    connection: MySQLConnection = Depends(get_db),
    _staff: dict[str, Any] = Depends(require_roles("receptionist", "admin")),
):
    """status=open (chờ + đang xử lý, mặc định), all, hoặc một trạng thái cụ thể."""
    cursor = connection.cursor(dictionary=True)
    try:
        if request_status in (None, "", "open"):
            where, params = " WHERE s.status IN ('pending', 'in_progress')", ()
        elif request_status == "all":
            where, params = "", ()
        elif request_status in SERVICE_STATUSES:
            where, params = " WHERE s.status = %s", (request_status,)
        else:
            raise HTTPException(status_code=400, detail="Trạng thái yêu cầu không hợp lệ.")
        cursor.execute(
            SERVICE_SELECT_SQL + where
            + " ORDER BY FIELD(s.status, 'pending', 'in_progress', 'completed', 'cancelled'), s.created_at DESC LIMIT 200",
            params,
        )
        items = [service_row_to_output(row) for row in rows_as_dict(cursor)]
        cursor.execute("SELECT COUNT(*) AS pending_count FROM service_requests WHERE status = 'pending'")
        return {"items": items, "pending_count": cursor.fetchone()["pending_count"]}
    finally:
        cursor.close()


@app.patch("/api/service-requests/{request_id}/status")
def update_service_request_status(
    request_id: int,
    payload: ServiceStatusInput,
    connection: MySQLConnection = Depends(get_db),
    staff: dict[str, Any] = Depends(require_roles("receptionist", "admin")),
):
    allowed_next = {
        "pending": {"in_progress", "cancelled"},
        "in_progress": {"completed", "cancelled"},
    }
    cursor = connection.cursor(dictionary=True)
    try:
        connection.rollback()
        connection.start_transaction()
        cursor.execute("SELECT id, status FROM service_requests WHERE id = %s FOR UPDATE", (request_id,))
        service = cursor.fetchone()
        if not service:
            raise HTTPException(status_code=404, detail="Không tìm thấy yêu cầu dịch vụ.")
        if payload.status not in allowed_next.get(service["status"], set()):
            raise HTTPException(
                status_code=409,
                detail=f"Không thể chuyển yêu cầu từ {service['status']} sang {payload.status}.",
            )
        cursor.execute(
            "UPDATE service_requests SET status = %s, handled_by = %s WHERE id = %s",
            (payload.status, staff["id"], request_id),
        )
        connection.commit()
        cursor.execute(SERVICE_SELECT_SQL + " WHERE s.id = %s", (request_id,))
        return service_row_to_output(cursor.fetchone())
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


# ==========================================================
# Quản lý đơn: chi tiết, sửa, hủy, đặt cọc
# ==========================================================
DEPOSIT_METHODS = {"cash": "Tiền mặt", "transfer": "Chuyển khoản", "card": "Thẻ"}


def log_booking_change(cursor, booking_id: int, action: str, detail: str, staff_id: int | None) -> None:
    cursor.execute(
        "INSERT INTO booking_changes (booking_id, action, detail, staff_id) VALUES (%s, %s, %s, %s)",
        (booking_id, action, detail[:1000], staff_id),
    )


def vnd(value: Any) -> str:
    return f"{int(Decimal(str(value or 0))):,}".replace(",", ".") + " ₫"


def load_booking(cursor, booking_id: int) -> dict[str, Any]:
    cursor.execute(BOOKING_SELECT_SQL + " WHERE b.id = %s", (booking_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn đặt phòng.")
    return booking_row_to_output(row)


@app.get("/api/bookings/{booking_id}")
def booking_detail(
    booking_id: int,
    connection: MySQLConnection = Depends(get_db),
    _staff: dict[str, Any] = Depends(require_roles("receptionist", "admin")),
):
    cursor = connection.cursor(dictionary=True)
    try:
        booking = load_booking(cursor, booking_id)
        cursor.execute(
            """SELECT ch.action, ch.detail, ch.created_at, u.full_name AS staff_name
               FROM booking_changes ch LEFT JOIN users u ON u.id = ch.staff_id
               WHERE ch.booking_id = %s ORDER BY ch.created_at DESC, ch.id DESC""",
            (booking_id,),
        )
        booking["changes"] = [
            {**row, "created_at": format_dt(row["created_at"])} for row in rows_as_dict(cursor)
        ]
        booking["deposit_method_label"] = DEPOSIT_METHODS.get(booking.get("deposit_method") or "", None)
        return booking
    finally:
        cursor.close()


@app.get("/api/bookings/{booking_id}/room-options")
def booking_room_options(
    booking_id: int,
    check_in: date,
    check_out: date,
    connection: MySQLConnection = Depends(get_db),
    _staff: dict[str, Any] = Depends(require_roles("receptionist", "admin")),
):
    """Các phòng còn trống cho khoảng ngày mới (không tính chính đơn này), để lễ tân đổi phòng khi sửa đơn."""
    if check_out <= check_in:
        raise HTTPException(status_code=400, detail="Ngày trả phòng phải sau ngày nhận phòng.")
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(
            """SELECT r.id, r.room_number, r.floor, rt.id AS room_type_id, rt.name AS room_type,
                      rt.base_price AS price_per_night, rt.max_guests
               FROM rooms r JOIN room_types rt ON rt.id = r.room_type_id
               WHERE r.is_active = TRUE AND rt.is_active = TRUE
                 AND """ + ROOM_SELLABLE_SQL + """
                 AND NOT EXISTS (
                     SELECT 1 FROM bookings b
                     WHERE b.room_id = r.id AND b.id <> %s
                       AND b.status IN ('pending', 'confirmed', 'checked_in')
                       AND b.check_in < %s AND b.check_out > %s
                 )
               ORDER BY rt.base_price, r.room_number""",
            (booking_id, check_out, check_in),
        )
        return rows_as_dict(cursor)
    finally:
        cursor.close()


class BookingEditInput(BaseModel):
    check_in: date
    check_out: date
    guests: int = Field(ge=1, le=20)
    room_id: int | None = Field(default=None, gt=0)
    note: str | None = Field(default=None, max_length=1000)
    customer_name: str | None = Field(default=None, min_length=2, max_length=120)
    customer_phone: str | None = Field(default=None, min_length=8, max_length=20)
    customer_email: str | None = Field(default=None, max_length=190)


@app.patch("/api/bookings/{booking_id}")
def edit_booking(
    booking_id: int,
    payload: BookingEditInput,
    connection: MySQLConnection = Depends(get_db),
    staff: dict[str, Any] = Depends(require_roles("receptionist", "admin")),
):
    """Sửa đơn. Chờ xác nhận / đã xác nhận: đổi ngày, số khách, phòng, thông tin khách.
    Đang lưu trú: chỉ đổi ngày trả phòng (gia hạn hoặc trả sớm), số khách, ghi chú, thông tin khách."""
    if payload.check_out <= payload.check_in:
        raise HTTPException(status_code=400, detail="Ngày trả phòng phải sau ngày nhận phòng.")
    cursor = connection.cursor(dictionary=True)
    try:
        connection.rollback()
        connection.start_transaction()
        cursor.execute(
            """SELECT b.id, b.status, b.room_id, b.check_in, b.check_out, b.guests, b.total_price,
                      b.note, b.customer_id, r.room_number
               FROM bookings b JOIN rooms r ON r.id = b.room_id
               WHERE b.id = %s FOR UPDATE""",
            (booking_id,),
        )
        current = cursor.fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Không tìm thấy đơn đặt phòng.")
        if current["status"] not in ("pending", "confirmed", "checked_in"):
            raise HTTPException(status_code=409, detail="Chỉ sửa được đơn đang chờ, đã xác nhận hoặc đang lưu trú.")

        staying = current["status"] == "checked_in"
        today = date.today()
        if staying:
            if payload.check_in != current["check_in"]:
                raise HTTPException(status_code=409, detail="Khách đã nhận phòng nên không đổi được ngày nhận phòng.")
            if payload.room_id and payload.room_id != current["room_id"]:
                raise HTTPException(status_code=409, detail="Khách đang ở nên không đổi phòng tại đây. Hãy trả phòng rồi tạo đơn mới nếu cần chuyển phòng.")
            if payload.check_out < today:
                raise HTTPException(status_code=400, detail="Ngày trả phòng mới không thể ở trong quá khứ.")
        elif payload.check_in != current["check_in"] and payload.check_in < today:
            raise HTTPException(status_code=400, detail="Ngày nhận phòng mới không thể ở trong quá khứ.")

        room_id = payload.room_id or current["room_id"]
        cursor.execute(
            """SELECT r.id, r.room_number, r.operational_status, rt.id AS room_type_id, rt.base_price, rt.max_guests
               FROM rooms r JOIN room_types rt ON rt.id = r.room_type_id
               WHERE r.id = %s AND r.is_active = TRUE FOR UPDATE""",
            (room_id,),
        )
        room = cursor.fetchone()
        if not room:
            raise HTTPException(status_code=404, detail="Không tìm thấy phòng.")
        if not staying and room["operational_status"] == "maintenance":
            raise HTTPException(status_code=409, detail=f"Phòng {room['room_number']} đang bảo trì.")
        if payload.guests > room["max_guests"]:
            raise HTTPException(status_code=400, detail=f"Phòng {room['room_number']} chỉ phục vụ tối đa {room['max_guests']} khách.")

        cursor.execute(
            """SELECT id FROM bookings
               WHERE room_id = %s AND id <> %s AND status IN ('pending', 'confirmed', 'checked_in')
                 AND check_in < %s AND check_out > %s LIMIT 1""",
            (room_id, booking_id, payload.check_out, payload.check_in),
        )
        if cursor.fetchone():
            if payload.room_id:
                raise HTTPException(status_code=409, detail=f"Phòng {room['room_number']} đã có đơn khác trong khoảng ngày này.")
            if staying:
                raise HTTPException(status_code=409, detail="Không gia hạn được: phòng đã có khách khác đặt trong những ngày này.")
            # Phòng cũ không còn trống cho ngày mới: tự chuyển sang phòng khác cùng hạng.
            other = find_free_room(cursor, room["room_type_id"], payload.check_in, payload.check_out, booking_id)
            if not other or payload.guests > other["max_guests"]:
                raise HTTPException(status_code=409, detail="Hạng phòng này đã hết phòng trống cho khoảng ngày mới.")
            room = {**room, "id": other["id"], "room_number": other["room_number"]}
            room_id = other["id"]

        nights = (payload.check_out - payload.check_in).days
        total_price = Decimal(str(room["base_price"])) * nights

        changes = []
        if payload.check_in != current["check_in"] or payload.check_out != current["check_out"]:
            changes.append(f"Ngày {current['check_in']:%d/%m}–{current['check_out']:%d/%m} → {payload.check_in:%d/%m}–{payload.check_out:%d/%m}")
        if room_id != current["room_id"]:
            changes.append(f"Phòng {current['room_number']} → {room['room_number']}")
        if payload.guests != current["guests"]:
            changes.append(f"Số khách {current['guests']} → {payload.guests}")
        if Decimal(str(current["total_price"])) != total_price:
            changes.append(f"Tiền phòng {vnd(current['total_price'])} → {vnd(total_price)}")
        note = (payload.note or "").strip() or None
        if note != (current["note"] or None):
            changes.append("Cập nhật ghi chú")

        cursor.execute(
            """UPDATE bookings SET check_in = %s, check_out = %s, guests = %s, room_id = %s,
                      total_price = %s, note = %s
               WHERE id = %s""",
            (payload.check_in, payload.check_out, payload.guests, room_id, total_price, note, booking_id),
        )
        if current["customer_id"] and (payload.customer_name or payload.customer_phone or payload.customer_email):
            cursor.execute("SELECT full_name, phone, email FROM customers WHERE id = %s", (current["customer_id"],))
            old = cursor.fetchone() or {}
            name = " ".join((payload.customer_name or old.get("full_name") or "").split())
            phone = (payload.customer_phone or old.get("phone") or "").strip()
            email = (payload.customer_email or old.get("email") or "").strip() or None
            if (name, phone, email) != (old.get("full_name"), old.get("phone"), old.get("email")):
                cursor.execute(
                    "UPDATE customers SET full_name = %s, phone = %s, email = %s WHERE id = %s",
                    (name, phone, email, current["customer_id"]),
                )
                changes.append("Cập nhật thông tin khách")
        # Phòng đang có khách: đồng bộ trạng thái phòng nếu đổi phòng trước khi nhận (không áp dụng khi đang ở)
        if changes:
            log_booking_change(cursor, booking_id, "edit", "; ".join(changes), staff["id"])
        connection.commit()
        return load_booking(cursor, booking_id)
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


class BookingCancelInput(BaseModel):
    reason: str = Field(min_length=3, max_length=255)
    deposit_action: Literal["refund", "keep"] | None = None


@app.post("/api/bookings/{booking_id}/cancel")
def cancel_booking(
    booking_id: int,
    payload: BookingCancelInput,
    connection: MySQLConnection = Depends(get_db),
    staff: dict[str, Any] = Depends(require_roles("receptionist", "admin")),
):
    cursor = connection.cursor(dictionary=True)
    try:
        connection.rollback()
        connection.start_transaction()
        cursor.execute("SELECT id, status FROM bookings WHERE id = %s FOR UPDATE", (booking_id,))
        booking = cursor.fetchone()
        if not booking:
            raise HTTPException(status_code=404, detail="Không tìm thấy đơn đặt phòng.")
        if booking["status"] not in ("pending", "confirmed"):
            raise HTTPException(status_code=409, detail="Chỉ hủy được đơn chưa nhận phòng. Khách đang ở thì dùng Trả phòng.")
        cursor.execute("SELECT amount, status FROM booking_deposits WHERE booking_id = %s FOR UPDATE", (booking_id,))
        deposit = cursor.fetchone()
        detail = f"Hủy đơn. Lý do: {payload.reason.strip()}"
        if deposit and deposit["status"] == "paid":
            if not payload.deposit_action:
                raise HTTPException(status_code=400, detail="Đơn đã đặt cọc. Hãy chọn hoàn cọc hoặc giữ cọc.")
            new_status = "refunded" if payload.deposit_action == "refund" else "forfeited"
            cursor.execute(
                "UPDATE booking_deposits SET status = %s, settled_at = NOW() WHERE booking_id = %s",
                (new_status, booking_id),
            )
            detail += f". {'Hoàn' if new_status == 'refunded' else 'Giữ'} cọc {vnd(deposit['amount'])}"
        cursor.execute(
            "UPDATE bookings SET status = 'cancelled', reviewed_by = %s, reviewed_at = %s WHERE id = %s",
            (staff["id"], datetime.now(), booking_id),
        )
        log_booking_change(cursor, booking_id, "cancel", detail, staff["id"])
        connection.commit()
        return load_booking(cursor, booking_id)
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


class DepositInput(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    method: Literal["cash", "transfer", "card"]
    note: str | None = Field(default=None, max_length=255)
    confirm_booking: bool = False


@app.post("/api/bookings/{booking_id}/deposit")
def record_deposit(
    booking_id: int,
    payload: DepositInput,
    connection: MySQLConnection = Depends(get_db),
    staff: dict[str, Any] = Depends(require_roles("receptionist", "admin")),
):
    """Ghi nhận (hoặc sửa) tiền đặt cọc. Có thể xác nhận đơn chờ cùng lúc."""
    cursor = connection.cursor(dictionary=True)
    try:
        connection.rollback()
        connection.start_transaction()
        cursor.execute("SELECT id, status, total_price FROM bookings WHERE id = %s FOR UPDATE", (booking_id,))
        booking = cursor.fetchone()
        if not booking:
            raise HTTPException(status_code=404, detail="Không tìm thấy đơn đặt phòng.")
        if booking["status"] not in ("pending", "confirmed", "checked_in"):
            raise HTTPException(status_code=409, detail="Chỉ nhận cọc cho đơn chưa trả phòng và chưa hủy.")
        if payload.amount > Decimal(str(booking["total_price"])):
            raise HTTPException(status_code=400, detail=f"Tiền cọc không được vượt tiền phòng ({vnd(booking['total_price'])}).")
        cursor.execute("SELECT amount, status FROM booking_deposits WHERE booking_id = %s FOR UPDATE", (booking_id,))
        existing = cursor.fetchone()
        if existing and existing["status"] != "paid":
            raise HTTPException(status_code=409, detail="Tiền cọc của đơn này đã được tất toán.")
        note = (payload.note or "").strip() or None
        if existing:
            cursor.execute(
                """UPDATE booking_deposits SET amount = %s, method = %s, note = %s, received_by = %s, received_at = NOW()
                   WHERE booking_id = %s""",
                (payload.amount, payload.method, note, staff["id"], booking_id),
            )
            detail = f"Sửa tiền cọc {vnd(existing['amount'])} → {vnd(payload.amount)} ({DEPOSIT_METHODS[payload.method]})"
        else:
            cursor.execute(
                """INSERT INTO booking_deposits (booking_id, amount, method, note, status, received_by)
                   VALUES (%s, %s, %s, %s, 'paid', %s)""",
                (booking_id, payload.amount, payload.method, note, staff["id"]),
            )
            detail = f"Nhận cọc {vnd(payload.amount)} ({DEPOSIT_METHODS[payload.method]})"
        if note:
            detail += f". {note}"
        if payload.confirm_booking and booking["status"] == "pending":
            cursor.execute(
                "UPDATE bookings SET status = 'confirmed', reviewed_by = %s, reviewed_at = %s WHERE id = %s",
                (staff["id"], datetime.now(), booking_id),
            )
            detail += ". Xác nhận đơn"
        log_booking_change(cursor, booking_id, "deposit", detail, staff["id"])
        connection.commit()
        return load_booking(cursor, booking_id)
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


# ==========================================================
# Hóa đơn khi trả phòng
# ==========================================================
# Quy tắc tính tiền dịch vụ khi trả phòng:
# - "completed" và "in_progress": tính vào hóa đơn (đang xử lý coi như đã phục vụ, chuyển thành hoàn tất).
# - "pending": khách chưa được phục vụ nên không tính, tự chuyển thành "cancelled".
# - "cancelled": không tính.
def build_invoice(cursor, booking_id: int) -> dict[str, Any]:
    """Tính hóa đơn từ dữ liệu hiện tại (chưa lưu)."""
    cursor.execute(BOOKING_SELECT_SQL + " WHERE b.id = %s", (booking_id,))
    booking = cursor.fetchone()
    if not booking:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn đặt phòng.")

    nights = max(1, (booking["check_out"] - booking["check_in"]).days)
    room_amount = Decimal(str(booking["total_price"]))
    room_line = {
        "description": f"{booking['room_type']}, phòng {booking['room_number']}",
        "detail": f"{booking['check_in'].strftime('%d/%m/%Y')} đến {booking['check_out'].strftime('%d/%m/%Y')}",
        "quantity": nights,
        "unit": "đêm",
        "unit_price": money_value(room_amount / nights),
        "amount": money_value(room_amount),
    }

    cursor.execute(
        SERVICE_SELECT_SQL + " WHERE s.booking_id = %s ORDER BY s.created_at ASC, s.id ASC",
        (booking_id,),
    )
    service_lines: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    service_amount = Decimal("0")
    for row in rows_as_dict(cursor):
        request = service_row_to_output(row, include_staff_fields=False)
        for item in request["items"]:
            line = {
                "date": request["created_at"],
                "category": request["category_label"],
                "description": item["name"],
                "quantity": item["quantity"],
                "unit_price": item["unit_price"],
                "amount": item["unit_price"] * item["quantity"],
                "request_status": request["status"],
            }
            if request["status"] in ("completed", "in_progress"):
                service_lines.append(line)
                service_amount += Decimal(str(line["amount"]))
            elif request["status"] == "pending":
                excluded.append(line)

    deposit_paid = Decimal("0")
    if booking.get("deposit_status") in ("paid", "applied") and booking.get("deposit_amount") is not None:
        deposit_paid = Decimal(str(booking["deposit_amount"]))
    total = room_amount + service_amount
    return {
        "deposit_paid": money_value(deposit_paid),
        "deposit_method": booking.get("deposit_method"),
        "amount_due": money_value(total - deposit_paid),
        "booking_id": booking["id"],
        "booking_code": booking["booking_code"],
        "customer_name": booking["customer_name"],
        "customer_phone": booking["customer_phone"],
        "customer_email": booking["customer_email"],
        "room_number": booking["room_number"],
        "room_type": booking["room_type"],
        "check_in": booking["check_in"].isoformat(),
        "check_out": booking["check_out"].isoformat(),
        "guests": booking["guests"],
        "nights": nights,
        "room_line": room_line,
        "service_lines": service_lines,
        "excluded_pending": excluded,
        "room_amount": money_value(room_amount),
        "service_amount": money_value(service_amount),
        "total_amount": money_value(room_amount + service_amount),
    }


def finalize_checkout_invoice(cursor, booking_id: int, staff_id: int) -> None:
    """Gọi bên trong transaction trả phòng: chốt dịch vụ rồi lưu hóa đơn."""
    cursor.execute(
        "UPDATE service_requests SET status = 'cancelled' WHERE booking_id = %s AND status = 'pending'",
        (booking_id,),
    )
    cursor.execute(
        "UPDATE service_requests SET status = 'completed', handled_by = %s WHERE booking_id = %s AND status = 'in_progress'",
        (staff_id, booking_id),
    )
    invoice = build_invoice(cursor, booking_id)
    invoice["excluded_pending"] = []
    cursor.execute(
        "UPDATE booking_deposits SET status = 'applied', settled_at = NOW() WHERE booking_id = %s AND status = 'paid'",
        (booking_id,),
    )
    issued_at = datetime.now()
    invoice_number = f"HD{issued_at:%Y%m%d}-{booking_id:05d}"
    invoice["invoice_number"] = invoice_number
    invoice["issued_at"] = format_dt(issued_at)
    cursor.execute(
        """INSERT INTO invoices
           (booking_id, invoice_number, room_amount, service_amount, total_amount, data_json, issued_by, issued_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            booking_id, invoice_number, invoice["room_amount"], invoice["service_amount"],
            invoice["total_amount"], json.dumps(invoice, ensure_ascii=False), staff_id, issued_at,
        ),
    )


@app.get("/api/bookings/{booking_id}/invoice-preview")
def invoice_preview(
    booking_id: int,
    connection: MySQLConnection = Depends(get_db),
    _staff: dict[str, Any] = Depends(require_roles("receptionist", "admin")),
):
    """Hóa đơn tạm tính để lễ tân kiểm tra với khách trước khi bấm trả phòng."""
    cursor = connection.cursor(dictionary=True)
    try:
        invoice = build_invoice(cursor, booking_id)
        invoice["invoice_number"] = None
        invoice["issued_at"] = None
        return invoice
    finally:
        cursor.close()


@app.get("/api/bookings/{booking_id}/invoice")
def get_invoice(
    booking_id: int,
    connection: MySQLConnection = Depends(get_db),
    _staff: dict[str, Any] = Depends(require_roles("receptionist", "admin")),
):
    """Hóa đơn đã xuất lúc trả phòng (bản lưu, không tính lại)."""
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(
            """SELECT i.data_json, u.full_name AS issued_by_name
               FROM invoices i LEFT JOIN users u ON u.id = i.issued_by
               WHERE i.booking_id = %s""",
            (booking_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Đơn này chưa có hóa đơn. Hóa đơn được xuất khi trả phòng.")
        invoice = json.loads(row["data_json"])
        invoice["issued_by_name"] = row["issued_by_name"]
        return invoice
    finally:
        cursor.close()


# ===== Thời tiết cho trang Khám phá Đà Lạt =====
# Máy chủ lấy dự báo từ Open-Meteo (miễn phí, không cần khóa API) rồi lưu tạm trong bộ nhớ,
# nên mọi khách xem trang dùng chung một bản dự báo và Open-Meteo chỉ bị gọi tối đa mỗi giờ
# một lần cho mỗi khu vực. Khi Open-Meteo lỗi, API trả bản cũ kèm cờ "stale" thay vì báo lỗi.
WEATHER_AREAS: dict[str, dict[str, Any]] = {
    "da-lat": {"name": "Đà Lạt", "name_en": "Da Lat", "lat": 11.9404, "lng": 108.4583},
    "langbiang": {"name": "Langbiang", "name_en": "Langbiang", "lat": 12.0470, "lng": 108.4420},
    "tuyen-lam": {"name": "Hồ Tuyền Lâm", "name_en": "Tuyen Lam Lake", "lat": 11.8880, "lng": 108.4280},
    "cau-dat": {"name": "Cầu Đất", "name_en": "Cau Dat", "lat": 11.7740, "lng": 108.5560},
}
WEATHER_CACHE_SECONDS = 60 * 60
WEATHER_STALE_LIMIT_SECONDS = 24 * 60 * 60
_weather_cache: dict[str, dict[str, Any]] = {}


def fetch_open_meteo(area: dict[str, Any]) -> dict[str, Any]:
    from urllib.parse import urlencode
    from urllib.request import Request as UrlRequest, urlopen

    params = urlencode({
        "latitude": area["lat"],
        "longitude": area["lng"],
        "timezone": "Asia/Ho_Chi_Minh",
        "forecast_days": 7,
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m,wind_gusts_10m",
        "hourly": "precipitation_probability",
    })
    request = UrlRequest(
        f"https://api.open-meteo.com/v1/forecast?{params}",
        headers={"User-Agent": "GrandviewHotel/0.1 (student project)"},
    )
    with urlopen(request, timeout=8) as response:  # noqa: S310 - fixed https host
        data = json.loads(response.read().decode("utf-8"))
    if "daily" not in data or "current" not in data:
        raise ValueError("Open-Meteo trả về dữ liệu không đầy đủ")
    return data


@app.get("/api/weather")
def weather_forecast(area: str = Query("da-lat", max_length=20)):
    if area not in WEATHER_AREAS:
        raise HTTPException(status_code=404, detail="Không có khu vực dự báo này.")
    now = datetime.now(timezone.utc)
    cached = _weather_cache.get(area)
    if cached and (now - cached["fetched_at"]).total_seconds() < WEATHER_CACHE_SECONDS:
        return {**cached["payload"], "stale": False}
    try:
        data = fetch_open_meteo(WEATHER_AREAS[area])
    except Exception:
        if cached and (now - cached["fetched_at"]).total_seconds() < WEATHER_STALE_LIMIT_SECONDS:
            return {**cached["payload"], "stale": True}
        raise HTTPException(status_code=503, detail="Chưa lấy được dự báo thời tiết. Vui lòng thử lại sau.")
    payload = {
        "area": area,
        "area_name": WEATHER_AREAS[area]["name"],
        "area_name_en": WEATHER_AREAS[area]["name_en"],
        "fetched_at": now.isoformat(),
        "source": "Open-Meteo",
        "daily": data["daily"],
        "current": data["current"],
        "hourly": data.get("hourly", {}),
    }
    _weather_cache[area] = {"fetched_at": now, "payload": payload}
    return {**payload, "stale": False}


# Serve the existing HTML/CSS/JS on the same origin as the API.
# API routes above are registered first, so /api/... is not swallowed by static serving.
app.mount("/", StaticFiles(directory=Path(__file__).resolve().parents[1] / "frontend", html=True), name="frontend")