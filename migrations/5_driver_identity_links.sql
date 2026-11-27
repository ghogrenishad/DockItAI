-- dockit: unified-authoritative
ALTER TABLE drivers ADD COLUMN email TEXT;

CREATE UNIQUE INDEX ux_drivers_email
ON drivers(email)
WHERE email IS NOT NULL;

CREATE TABLE driver_identity_links (
    cognito_subject TEXT PRIMARY KEY,
    driver_id TEXT NOT NULL UNIQUE,
    linked_at_utc TEXT NOT NULL,
    FOREIGN KEY (driver_id) REFERENCES drivers(driver_id)
);
