from collections.abc import Generator

import mysql.connector
from mysql.connector import MySQLConnection
from fastapi import HTTPException

from app import config


def connect_db() -> MySQLConnection:
    """Create a new MySQL connection for one request or task."""
    return mysql.connector.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        database=config.DB_NAME,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        autocommit=False,
        connection_timeout=5,
    )


def get_db() -> Generator[MySQLConnection, None, None]:
    connection = None
    try:
        connection = connect_db()
        yield connection
    except mysql.connector.Error as exc:
        if connection and connection.is_connected():
            connection.rollback()
        raise HTTPException(
            status_code=503,
            detail="Không thể kết nối cơ sở dữ liệu. Hãy kiểm tra MySQL và file .env.",
        ) from exc
    finally:
        if connection and connection.is_connected():
            connection.close()
