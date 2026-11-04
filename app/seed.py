"""Insert demo room types, rooms, and a staff account into the configured database.

Run: python -m app.seed
Demo logins (local only):
- Admin: admin@grandview.com / Admin12345!
- Receptionist: reception@grandview.com / Reception123!
Change these passwords before any real deployment.
"""
import json

from app.db import connect_db
from app.security import hash_password

# Ảnh tạm vẽ theo bộ nhận diện, nằm trong frontend/images.
# Khi có ảnh chụp thật, chỉ cần thay đường dẫn trong gallery và image_url.
ROOM_TYPES = [
    {
        "name": "Phòng Deluxe",
        "description": "Không gian nghỉ dưỡng tinh tế, phù hợp cho kỳ nghỉ thư giãn.",
        "base_price": 2500000,
        "max_guests": 2,
        "area_sqm": 32,
        "bed_type": "1 giường đôi 1m6",
        "view_name": "Nhìn ra đồi thông",
        "layout_summary": "Phòng ngủ, bàn làm việc, phòng tắm vòi sen và ban công nhỏ.",
        "amenities": "Điều hòa, Tivi, Két an toàn, Ấm đun nước, Wifi, Đồ vệ sinh cá nhân",
        "image_url": "/images/phong-deluxe.svg",
        "gallery": ["/images/so-do-deluxe.svg"],
    },
    {
        "name": "Phòng Superior",
        "description": "Phòng nghỉ tiện nghi với thiết kế hiện đại.",
        "base_price": 3200000,
        "max_guests": 2,
        "area_sqm": 40,
        "bed_type": "1 giường đôi 1m8",
        "view_name": "Nhìn ra đồi thông và hồ bơi",
        "layout_summary": "Phòng ngủ rộng, ghế đọc sách, phòng tắm có bồn và ban công rộng.",
        "amenities": "Điều hòa, Tivi, Bồn tắm, Máy pha cà phê, Két an toàn, Wifi, Áo choàng tắm",
        "image_url": "/images/phong-superior.svg",
        "gallery": ["/images/so-do-superior.svg"],
    },
    {
        "name": "Suite",
        "description": "Không gian rộng rãi với khu vực sinh hoạt riêng.",
        "base_price": 5500000,
        "max_guests": 3,
        "area_sqm": 62,
        "bed_type": "1 giường đôi 1m8 và 1 sofa giường",
        "view_name": "Góc nhìn hai hướng ra rừng thông",
        "layout_summary": "Phòng ngủ riêng, phòng khách, bếp nhỏ, phòng tắm có bồn và ban công.",
        "amenities": "Điều hòa, 2 tivi, Bếp nhỏ, Bồn tắm, Máy pha cà phê, Két an toàn, Wifi",
        "image_url": "/images/phong-suite.svg",
        "gallery": ["/images/so-do-suite.svg"],
    },
    {
        "name": "Biệt thự",
        "description": "Không gian riêng tư cao cấp cho gia đình và nhóm bạn.",
        "base_price": 8900000,
        "max_guests": 5,
        "area_sqm": 95,
        "bed_type": "2 phòng ngủ, 1 giường đôi và 2 giường đơn",
        "view_name": "Sân vườn riêng giữa rừng thông",
        "layout_summary": "Hai phòng ngủ, phòng khách liền bếp, hai phòng tắm và sân vườn riêng.",
        "amenities": "Điều hòa, Bếp đầy đủ, Máy giặt, Sân vườn riêng, Chỗ đỗ xe, Wifi, Bồn tắm",
        "image_url": "/images/phong-villa.svg",
        "gallery": ["/images/so-do-villa.svg"],
    },
]


def main():
    connection = connect_db()
    cursor = connection.cursor(dictionary=True)
    try:
        for room_type in ROOM_TYPES:
            gallery = json.dumps(room_type["gallery"], ensure_ascii=False)
            cursor.execute(
                """INSERT INTO room_types
                       (name, description, base_price, max_guests, image_url,
                        area_sqm, bed_type, view_name, layout_summary, amenities, gallery_json)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE description = VALUES(description), base_price = VALUES(base_price),
                     max_guests = VALUES(max_guests),
                     image_url = COALESCE(image_url, VALUES(image_url)),
                     area_sqm = VALUES(area_sqm), bed_type = VALUES(bed_type), view_name = VALUES(view_name),
                     layout_summary = VALUES(layout_summary), amenities = VALUES(amenities),
                     gallery_json = VALUES(gallery_json)""",
                (room_type["name"], room_type["description"], room_type["base_price"],
                 room_type["max_guests"], room_type["image_url"], room_type["area_sqm"],
                 room_type["bed_type"], room_type["view_name"], room_type["layout_summary"],
                 room_type["amenities"], gallery),
            )
        connection.commit()

        cursor.execute("SELECT id, name FROM room_types")
        type_ids = {row["name"]: row["id"] for row in cursor.fetchall()}
        room_groups = [
            ("Phòng Deluxe", ["101", "102", "103"]),
            ("Phòng Superior", ["201", "202", "203"]),
            ("Suite", ["301", "302"]),
            ("Biệt thự", ["V01", "V02"]),
        ]
        for type_name, room_numbers in room_groups:
            for room_number in room_numbers:
                cursor.execute(
                    """INSERT INTO rooms (room_number, room_type_id, floor, operational_status)
                       VALUES (%s, %s, %s, 'available')
                       ON DUPLICATE KEY UPDATE room_type_id = VALUES(room_type_id)""",
                    (room_number, type_ids[type_name], int(room_number[0]) if room_number[0].isdigit() else 1),
                )
        connection.commit()

        cursor.execute("SELECT id FROM users WHERE email = %s", ("admin@grandview.com",))
        if not cursor.fetchone():
            cursor.execute(
                """INSERT INTO users (full_name, email, password_hash, role)
                   VALUES (%s, %s, %s, 'admin')""",
                ("Quản trị viên", "admin@grandview.com", hash_password("Admin12345!")),
            )
            connection.commit()

        cursor.execute("SELECT id FROM users WHERE email = %s", ("reception@grandview.com",))
        if not cursor.fetchone():
            cursor.execute(
                """INSERT INTO users (full_name, email, password_hash, role)
                   VALUES (%s, %s, %s, 'receptionist')""",
                ("Lễ tân Grandview", "reception@grandview.com", hash_password("Reception123!")),
            )
            connection.commit()

        print("Đã thêm dữ liệu mẫu cho loại phòng, phòng và tài khoản vận hành.")
        print("Admin demo: admin@grandview.com / Admin12345!")
        print("Lễ tân demo: reception@grandview.com / Reception123!")
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


if __name__ == "__main__":
    main()
