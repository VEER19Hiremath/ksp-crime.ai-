-- Crime AI (KSP Hackathon) — PostgreSQL schema
-- Derived from "Police FIR System — ER Diagram" (Police_FIR_ER_Diagram (1).pdf)
-- Target: Neon PostgreSQL. Run pgvector migration (02_pgvector.sql) after this file.

BEGIN;

-- ============================================================
-- Reference / lookup tables (no dependencies)
-- ============================================================

CREATE TABLE state (
    state_id        INT PRIMARY KEY,
    state_name      VARCHAR(100) NOT NULL,
    nationality_id  INT,
    active          BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE district (
    district_id     INT PRIMARY KEY,
    district_name   VARCHAR(100) NOT NULL,
    state_id        INT NOT NULL REFERENCES state(state_id),
    active          BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE unit_type (
    unit_type_id     INT PRIMARY KEY,
    unit_type_name   VARCHAR(100) NOT NULL,   -- e.g. Police Station, Circle Office
    city_dist_state  VARCHAR(20),             -- operational level: City / District / State
    hierarchy        INT,
    active           BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE unit (
    unit_id         INT PRIMARY KEY,
    unit_name       VARCHAR(150) NOT NULL,
    type_id         INT REFERENCES unit_type(unit_type_id),
    parent_unit     INT REFERENCES unit(unit_id),   -- self-reference for hierarchy
    nationality_id  INT,
    state_id        INT REFERENCES state(state_id),
    district_id     INT REFERENCES district(district_id),
    active          BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE rank_master (
    rank_id     INT PRIMARY KEY,
    rank_name   VARCHAR(100) NOT NULL,   -- Constable, Inspector, DSP, ...
    hierarchy   INT,
    active      BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE designation (
    designation_id    INT PRIMARY KEY,
    designation_name  VARCHAR(100) NOT NULL,  -- Investigating Officer, SHO, ...
    active            BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order        INT
);

CREATE TABLE case_category (
    case_category_id  INT PRIMARY KEY,
    lookup_value       VARCHAR(50) NOT NULL   -- FIR, UDR, PAR, Zero FIR
);

CREATE TABLE gravity_offence (
    gravity_offence_id INT PRIMARY KEY,
    lookup_value        VARCHAR(50) NOT NULL  -- Heinous, Non-Heinous
);

CREATE TABLE crime_head (
    crime_head_id     INT PRIMARY KEY,
    crime_group_name  VARCHAR(150) NOT NULL, -- e.g. Crimes Against Body
    active            BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE crime_sub_head (
    crime_sub_head_id  INT PRIMARY KEY,
    crime_head_id      INT NOT NULL REFERENCES crime_head(crime_head_id),
    crime_head_name    VARCHAR(150) NOT NULL, -- e.g. Murder, Robbery
    seq_id             INT
);

CREATE TABLE act (
    act_code         VARCHAR(20) PRIMARY KEY,  -- e.g. IPC, NDPS
    act_description  VARCHAR(200) NOT NULL,
    short_name       VARCHAR(50),
    active           BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE section (
    act_code             VARCHAR(20) NOT NULL REFERENCES act(act_code),
    section_code         VARCHAR(20) NOT NULL,   -- e.g. 302, 307
    section_description  VARCHAR(300),
    active               BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (act_code, section_code)
);

CREATE TABLE crime_head_act_section (
    crime_head_id  INT NOT NULL REFERENCES crime_head(crime_head_id),
    act_code       VARCHAR(20) NOT NULL,
    section_code   VARCHAR(20) NOT NULL,
    PRIMARY KEY (crime_head_id, act_code, section_code),
    FOREIGN KEY (act_code, section_code) REFERENCES section(act_code, section_code)
);

CREATE TABLE case_status_master (
    case_status_id    INT PRIMARY KEY,
    case_status_name  VARCHAR(50) NOT NULL  -- Under Investigation, Charge Sheeted, Closed
);

CREATE TABLE caste_master (
    caste_master_id    INT PRIMARY KEY,
    caste_master_name  VARCHAR(100) NOT NULL
);

CREATE TABLE religion_master (
    religion_id    INT PRIMARY KEY,
    religion_name  VARCHAR(100) NOT NULL
);

CREATE TABLE occupation_master (
    occupation_id    INT PRIMARY KEY,
    occupation_name  VARCHAR(100) NOT NULL
);

CREATE TABLE court (
    court_id     INT PRIMARY KEY,
    court_name   VARCHAR(200) NOT NULL,
    district_id  INT REFERENCES district(district_id),
    state_id     INT REFERENCES state(state_id),
    active       BOOLEAN NOT NULL DEFAULT TRUE
);

-- ============================================================
-- People
-- ============================================================

CREATE TABLE employee (
    employee_id            INT PRIMARY KEY,
    district_id            INT REFERENCES district(district_id),
    unit_id                INT REFERENCES unit(unit_id),
    rank_id                INT REFERENCES rank_master(rank_id),
    designation_id         INT REFERENCES designation(designation_id),
    kgid                   VARCHAR(30),   -- Karnataka Government ID
    first_name             VARCHAR(100) NOT NULL,
    employee_dob           DATE,
    gender_id              SMALLINT,
    blood_group_id         SMALLINT,
    physically_challenged  BOOLEAN NOT NULL DEFAULT FALSE,
    appointment_date       DATE
);

-- ============================================================
-- Core case tables
-- ============================================================

CREATE TABLE case_master (
    case_master_id         INT PRIMARY KEY,
    crime_no               VARCHAR(30) NOT NULL,   -- 1(cat)+4(district)+4(unit)+4(year)+5(serial)
    case_no                VARCHAR(20) NOT NULL,    -- YYYY + 5-digit serial
    crime_registered_date  DATE NOT NULL,
    police_person_id       INT REFERENCES employee(employee_id),      -- officer who registered FIR
    police_station_id      INT REFERENCES unit(unit_id),
    case_category_id       INT REFERENCES case_category(case_category_id),
    gravity_offence_id     INT REFERENCES gravity_offence(gravity_offence_id),
    crime_major_head_id    INT REFERENCES crime_head(crime_head_id),
    crime_minor_head_id    INT REFERENCES crime_sub_head(crime_sub_head_id),
    case_status_id         INT REFERENCES case_status_master(case_status_id),
    court_id               INT REFERENCES court(court_id),
    incident_from_date     TIMESTAMP,
    incident_to_date       TIMESTAMP,
    info_received_ps_date  TIMESTAMP,
    latitude               DECIMAL(9,6),
    longitude              DECIMAL(9,6),
    brief_facts            TEXT
);

CREATE INDEX idx_case_master_station ON case_master(police_station_id);
CREATE INDEX idx_case_master_status ON case_master(case_status_id);
CREATE INDEX idx_case_master_crime_head ON case_master(crime_major_head_id, crime_minor_head_id);
CREATE INDEX idx_case_master_date ON case_master(crime_registered_date);
CREATE INDEX idx_case_master_geo ON case_master(latitude, longitude);

CREATE TABLE complainant_details (
    complainant_id    INT PRIMARY KEY,
    case_master_id    INT NOT NULL REFERENCES case_master(case_master_id),
    complainant_name  VARCHAR(150) NOT NULL,
    age_year          INT,
    occupation_id     INT REFERENCES occupation_master(occupation_id),
    religion_id       INT REFERENCES religion_master(religion_id),
    caste_id          INT REFERENCES caste_master(caste_master_id),
    gender_id         SMALLINT
);

CREATE TABLE act_section_association (
    case_master_id    INT NOT NULL REFERENCES case_master(case_master_id),
    act_id            VARCHAR(20) NOT NULL,
    section_id        VARCHAR(20) NOT NULL,
    act_order_id      INT,
    section_order_id  INT,
    PRIMARY KEY (case_master_id, act_id, section_id),
    FOREIGN KEY (act_id, section_id) REFERENCES section(act_code, section_code)
);

CREATE TABLE victim (
    victim_master_id  INT PRIMARY KEY,
    case_master_id    INT NOT NULL REFERENCES case_master(case_master_id),
    victim_name       VARCHAR(150) NOT NULL,
    age_year          INT,
    gender_id         SMALLINT,      -- lookup: m, f, t
    victim_police     BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE accused (
    accused_master_id  INT PRIMARY KEY,
    case_master_id     INT NOT NULL REFERENCES case_master(case_master_id),
    accused_name       VARCHAR(150) NOT NULL,
    age_year           INT,
    gender_id          SMALLINT,     -- M/F/T
    person_id          VARCHAR(10)   -- sort label: A1, A2, A3...
);

CREATE INDEX idx_accused_case ON accused(case_master_id);
CREATE INDEX idx_accused_name ON accused(accused_name);

CREATE TABLE arrest_surrender (
    arrest_surrender_id         INT PRIMARY KEY,
    case_master_id              INT NOT NULL REFERENCES case_master(case_master_id),
    arrest_surrender_type_id    SMALLINT,   -- lookup: arrest / voluntary surrender
    arrest_surrender_date       DATE,
    arrest_surrender_state_id   INT REFERENCES state(state_id),
    arrest_surrender_district_id INT REFERENCES district(district_id),
    police_station_id           INT REFERENCES unit(unit_id),
    io_id                       INT REFERENCES employee(employee_id),
    court_id                    INT REFERENCES court(court_id),
    accused_master_id           INT REFERENCES accused(accused_master_id),
    is_accused                  BOOLEAN NOT NULL DEFAULT TRUE,
    is_complainant_accused      BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX idx_arrest_surrender_case ON arrest_surrender(case_master_id);
CREATE INDEX idx_arrest_surrender_io ON arrest_surrender(io_id);

-- Junction table per the ER diagram's relationship matrix (one arrest event
-- can, via this junction, be linked to multiple accused beyond the single
-- accused_master_id FK already on arrest_surrender).
CREATE TABLE inv_arrestsurrenderaccused (
    arrest_surrender_id  INT NOT NULL REFERENCES arrest_surrender(arrest_surrender_id),
    accused_master_id    INT NOT NULL REFERENCES accused(accused_master_id),
    PRIMARY KEY (arrest_surrender_id, accused_master_id)
);

CREATE TABLE chargesheet_details (
    cs_id             INT PRIMARY KEY,
    case_master_id    INT NOT NULL REFERENCES case_master(case_master_id),
    cs_date           TIMESTAMP,
    cs_type           CHAR(1),   -- A = Chargesheet, B = False Case, C = Undetected
    police_person_id  INT REFERENCES employee(employee_id)
);

CREATE INDEX idx_chargesheet_case ON chargesheet_details(case_master_id);

COMMIT;

-- NOTE: the ER diagram's relationship matrix references an "Inv_OccuranceTime"
-- table (one-to-one with CaseMaster) but the source document does not define
-- its columns. incident_from_date / incident_to_date / latitude / longitude
-- already live on case_master directly, so it is intentionally omitted here.
-- Add it later if the real KSP schema export defines extra columns for it.
