-- Lightweight role-based auth for the prototype (JWT-based, not Zoho Catalyst —
-- Catalyst needs an OAuth client created in its console, which wasn't available
-- yet; swap this out for Catalyst auth later without touching the rest of the
-- schema, since role-gating in the app only reads app_user.role).

CREATE TABLE app_user (
    user_id       SERIAL PRIMARY KEY,
    username      VARCHAR(50) UNIQUE NOT NULL,
    password_hash VARCHAR(100) NOT NULL,
    full_name     VARCHAR(150) NOT NULL,
    role          VARCHAR(20) NOT NULL CHECK (role IN ('Investigator', 'SHO', 'DSP', 'Analyst', 'Administrator')),
    employee_id   INT REFERENCES employee(employee_id),
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMP NOT NULL DEFAULT now()
);
