-- Run this once against your local MySQL80 instance (e.g. in MySQL Workbench,
-- HeidiSQL, or `mysql -u root -p < docs/create_database.sql`) as a user with
-- admin rights (root). It creates a dedicated, least-privilege app account so
-- the Django backend never needs your root credentials.

CREATE DATABASE IF NOT EXISTS securetap CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'securetap_app'@'localhost' IDENTIFIED BY 'VCVHD9FHhZbwwFVL82DExFtr';
GRANT ALL PRIVILEGES ON securetap.* TO 'securetap_app'@'localhost';
FLUSH PRIVILEGES;
