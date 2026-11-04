from datetime import date
from decimal import Decimal
from typing import Literal
import re

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class LoginInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserOutput(BaseModel):
    id: int
    full_name: str
    email: str
    role: str


class RoomOutput(BaseModel):
    id: int
    room_number: str
    floor: int | None
    operational_status: str
    room_type_id: int
    room_type: str
    description: str | None
    price_per_night: Decimal
    max_guests: int
    image_url: str | None

    model_config = ConfigDict(from_attributes=True)


class BookingInput(BaseModel):
    room_id: int = Field(gt=0)
    check_in: date
    check_out: date
    guests: int = Field(ge=1, le=20)
    customer_name: str = Field(min_length=2, max_length=120)
    customer_phone: str = Field(min_length=8, max_length=20)
    customer_email: EmailStr
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("customer_name")
    @classmethod
    def normalize_customer_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 2:
            raise ValueError("Vui lòng nhập họ tên hợp lệ.")
        return normalized

    @field_validator("customer_phone")
    @classmethod
    def validate_customer_phone(cls, value: str) -> str:
        normalized = value.strip()
        if not re.fullmatch(r"[0-9+().\-\s]{8,20}", normalized):
            raise ValueError("Số điện thoại chỉ được chứa chữ số và ký tự + ( ) . - .")
        digit_count = sum(character.isdigit() for character in normalized)
        if not 8 <= digit_count <= 15:
            raise ValueError("Số điện thoại phải có từ 8 đến 15 chữ số.")
        return normalized

    @field_validator("customer_email", mode="before")
    @classmethod
    def normalize_customer_email(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value

    @model_validator(mode="after")
    def validate_dates(self):
        if self.check_out <= self.check_in:
            raise ValueError("Ngày trả phòng phải sau ngày nhận phòng.")
        return self


class BookingStatusInput(BaseModel):
    status: Literal["confirmed", "rejected", "cancelled", "checked_in", "checked_out"]


class BookingOutput(BaseModel):
    id: int
    booking_code: str
    user_id: int | None = None
    customer_id: int | None = None
    customer_name: str | None = None
    customer_phone: str | None = None
    customer_email: str | None = None
    room_id: int
    room_number: str | None = None
    room_type: str | None = None
    check_in: date
    check_out: date
    guests: int
    total_price: Decimal
    note: str | None
    status: str
    created_at: str | None = None

class BookingLookupInput(BaseModel):
    booking_code: str = Field(min_length=6, max_length=40)

    @field_validator("booking_code")
    @classmethod
    def normalize_booking_code(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not re.fullmatch(r"[A-Z0-9-]{6,40}", normalized):
            raise ValueError("Mã đặt phòng không đúng định dạng.")
        return normalized


class BookingLookupOutput(BaseModel):
    booking_code: str
    customer_name: str
    customer_phone_masked: str | None = None
    room_number: str
    room_type: str
    check_in: date
    check_out: date
    guests: int
    total_price: Decimal
    note: str | None = None
    status: str
    created_at: str | None = None


class WalkInInput(BaseModel):
    """Lễ tân thêm khách đến thẳng quầy: nhận phòng hôm nay, cần thông tin khách."""
    room_id: int = Field(gt=0)
    customer_name: str = Field(min_length=2, max_length=120)
    customer_phone: str = Field(min_length=8, max_length=20)
    customer_email: EmailStr | None = None
    check_out: date
    guests: int = Field(ge=1, le=20)
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("customer_name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 2:
            raise ValueError("Vui lòng nhập họ tên hợp lệ.")
        return normalized

    @field_validator("customer_phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        normalized = value.strip()
        if not re.fullmatch(r"[0-9+().\-\s]{8,20}", normalized):
            raise ValueError("Số điện thoại chỉ được chứa chữ số và ký tự + ( ) . - .")
        digit_count = sum(character.isdigit() for character in normalized)
        if not 8 <= digit_count <= 15:
            raise ValueError("Số điện thoại phải có từ 8 đến 15 chữ số.")
        return normalized

    @field_validator("customer_email", mode="before")
    @classmethod
    def blank_email_is_none(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value.strip() if isinstance(value, str) else value
