-- Run this once against your local MySQL80 instance (e.g. in MySQL Workbench,
-- HeidiSQL, or `mysql -u root -p < docs/create_database.sql`) as a user with
-- admin rights (root). It creates a dedicated, least-privilege app account so
-- the Django backend never needs your root credentials.
--
-- Before running: replace CHANGE_ME_TO_A_STRONG_PASSWORD below with a
-- password you choose (e.g. `python -c "import secrets; print(secrets.token_urlsafe(24))"`),
-- then put that exact same password in backend/.env's DB_PASSWORD. See the
-- main README's "Getting your secrets" section for details.

CREATE DATABASE IF NOT EXISTS securetap CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'securetap_app'@'localhost' IDENTIFIED BY 'CHANGE_ME_TO_A_STRONG_PASSWORD';
GRANT ALL PRIVILEGES ON securetap.* TO 'securetap_app'@'localhost';
FLUSH PRIVILEGES;
