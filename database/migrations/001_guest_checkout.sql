-- Grandview Hotel: migrate existing database to public guest checkout.
-- Safe to run more than once on MySQL 8.4. Back up the database before applying.
-- Existing users and historical bookings are preserved. New guest bookings use customers.id;
-- user_id remains nullable only for compatibility with historical customer-account bookings.

USE hotel_management;

DROP PROCEDURE IF EXISTS migrate_guest_checkout;
DELIMITER $$
CREATE PROCEDURE migrate_guest_checkout()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_name = 'customers'
    ) THEN
        CREATE TABLE customers (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
            full_name VARCHAR(120) NOT NULL,
            phone VARCHAR(20) NOT NULL,
            email VARCHAR(190) NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            KEY idx_customers_phone (phone)
        ) ENGINE=InnoDB;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'bookings' AND column_name = 'customer_id'
    ) THEN
        ALTER TABLE bookings ADD COLUMN customer_id BIGINT UNSIGNED NULL AFTER user_id;
    END IF;

    -- Allow new public bookings to have no users row; preserve old user_id values.
    ALTER TABLE bookings MODIFY COLUMN user_id BIGINT UNSIGNED NULL;
    ALTER TABLE bookings MODIFY COLUMN booking_code VARCHAR(40) NOT NULL;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'bookings' AND index_name = 'idx_bookings_customer_created'
    ) THEN
        CREATE INDEX idx_bookings_customer_created ON bookings (customer_id, created_at);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.referential_constraints
        WHERE constraint_schema = DATABASE() AND table_name = 'bookings' AND constraint_name = 'fk_bookings_customer'
    ) THEN
        ALTER TABLE bookings
            ADD CONSTRAINT fk_bookings_customer FOREIGN KEY (customer_id) REFERENCES customers(id);
    END IF;
END$$
DELIMITER ;

CALL migrate_guest_checkout();
DROP PROCEDURE IF EXISTS migrate_guest_checkout;

SELECT 'Guest checkout migration completed.' AS result;
