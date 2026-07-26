"""Expands the sparse demo seed (4 cases) into a realistic-sized test dataset:
more districts/stations, more crime types, cases spread across ~15 months (for
dashboard trend charts), geo-jittered per station (for hotspot clustering), and
a set of repeat-offender names appearing across multiple cases (for the Neo4j
criminal-network graph). Pure stdlib (random/datetime) — no Faker dependency.

All new IDs start at offsets well above seed.sql's (district 1-4, unit 100-103,
case_master 1-4, etc.) so this is additive and safe to run after seed.sql.

Usage (from backend/, venv active so DATABASE_URL is picked up the same way):
    python ../database/generate_test_data.py
"""
import os
import random
import sys
from datetime import datetime, timedelta

import psycopg

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from core.config import get_settings  # noqa: E402

random.seed(42)

# --- Reference data: (district_id, district_name, [(unit_id, station_name, lat, lon)]) ---
DISTRICTS = [
    (10, "Belagavi", [(210, "Belagavi Camp PS", 15.8497, 74.4977), (211, "Belagavi Market PS", 15.8656, 74.5089)]),
    (11, "Dakshina Kannada", [(212, "Mangaluru City PS", 12.9141, 74.8560), (213, "Mangaluru North PS", 12.9345, 74.8460)]),
    (12, "Kalaburagi", [(214, "Kalaburagi Central PS", 17.3297, 76.8343), (215, "Kalaburagi Rural PS", 17.3400, 76.8100)]),
    (13, "Tumakuru", [(216, "Tumakuru City PS", 13.3392, 77.1139), (217, "Tumakuru Rural PS", 13.3500, 77.1300)]),
    (14, "Shivamogga", [(218, "Shivamogga Town PS", 13.9299, 75.5681)]),
]

# Existing districts from seed.sql, reused for extra stations/cases
EXISTING_UNITS = [
    (2, 100, "Hubballi Rural PS", 15.3647, 75.1240),
    (2, 101, "Hubballi City PS", 15.3550, 75.1330),
    (1, 102, "Cubbon Park PS", 12.9760, 77.5920),
    (3, 103, "Mysuru West PS", 12.2958, 76.6394),
]

CRIME_SUB_HEADS = [
    # (id, head_id, head_group_name, sub_head_name)
    (30, 1, "Crimes Against Body", "Kidnapping"),
    (31, 1, "Crimes Against Body", "Domestic Violence"),
    (32, 2, "Crimes Against Property", "Chain Snatching"),
    (33, 2, "Crimes Against Property", "Cheating/Fraud"),
    (34, 2, "Crimes Against Property", "Cybercrime"),
]
# plus existing: 10 Murder, 11 Assault, 20 Robbery, 21 Theft (from seed.sql)
ALL_CRIME_SUB_HEADS = [10, 11, 20, 21, 30, 31, 32, 33, 34]

ACT_SECTIONS = [("IPC", "392"), ("IPC", "302"), ("IPC", "379"), ("IPC", "323"), ("IPC", "420"), ("IPC", "366")]

FIRST_NAMES = [
    "Ravi", "Suresh", "Manjunath", "Prakash", "Anand", "Ganesh", "Vinay", "Shivakumar",
    "Nagaraj", "Basavaraj", "Kumar", "Santosh", "Mahesh", "Girish", "Chandan",
    "Lakshmi", "Sunita", "Kavya", "Divya", "Pooja", "Anitha", "Roopa", "Shwetha",
]
LAST_NAMES = ["Kulkarni", "Patil", "Rao", "Hegde", "Gowda", "Naik", "Reddy", "Shetty", "Desai", "Joshi"]

# A fixed set of repeat-offender names used across multiple cases, so the
# criminal-network graph (Accused-SAME_PERSON_AS / CO_ACCUSED_WITH) has
# something real to show beyond the single pair seed.sql already has.
REPEAT_OFFENDERS = [
    "Ramesh Naik", "Iqbal Sheikh", "Vijay Kumar", "Mahadev Gowda", "Yusuf Ali",
]

CASE_STATUS_IDS = [1, 1, 1, 2, 2, 3]  # weighted: mostly under investigation
GRAVITY_IDS = [1, 2]
CASE_CATEGORY_IDS = [1, 1, 1, 2, 3]  # mostly FIR
COURTS = [(10, "District & Sessions Court, Belagavi", 10), (11, "District & Sessions Court, Mangaluru", 11)]

BRIEF_FACTS_TEMPLATES = {
    10: "Victim found dead under suspicious circumstances near {loc}.",
    11: "Complainant assaulted by known persons following a dispute near {loc}.",
    20: "Complainant robbed of valuables near {loc}.",
    21: "Theft of property reported from {loc}.",
    30: "Victim reported missing, suspected kidnapping near {loc}.",
    31: "Domestic dispute escalated to physical violence at residence near {loc}.",
    32: "Gold chain snatched from complainant near {loc}.",
    33: "Complainant defrauded of money via a fake investment scheme.",
    34: "Complainant's bank account compromised via a phishing link.",
}


def random_date(start: datetime, end: datetime) -> datetime:
    delta = end - start
    return start + timedelta(seconds=random.randint(0, int(delta.total_seconds())))


def jitter(lat: float, lon: float) -> tuple[float, float]:
    return round(lat + random.uniform(-0.03, 0.03), 6), round(lon + random.uniform(-0.03, 0.03), 6)


def random_name() -> str:
    return f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"


def main():
    settings = get_settings()
    conn = psycopg.connect(settings.database_url)
    cur = conn.cursor()

    # --- Reference data ---
    for district_id, name, _units in DISTRICTS:
        cur.execute(
            "INSERT INTO district (district_id, district_name, state_id, active) VALUES (%s,%s,1,TRUE) "
            "ON CONFLICT (district_id) DO NOTHING",
            (district_id, name),
        )
    for _district_id, name, units in DISTRICTS:
        for unit_id, unit_name, lat, lon in units:
            cur.execute(
                "INSERT INTO unit (unit_id, unit_name, type_id, state_id, district_id, active) "
                "VALUES (%s,%s,1,1,%s,TRUE) ON CONFLICT (unit_id) DO NOTHING",
                (unit_id, unit_name, _district_id),
            )
    for sub_id, head_id, _group, name in CRIME_SUB_HEADS:
        cur.execute(
            "INSERT INTO crime_sub_head (crime_sub_head_id, crime_head_id, crime_head_name, seq_id) "
            "VALUES (%s,%s,%s,1) ON CONFLICT (crime_sub_head_id) DO NOTHING",
            (sub_id, head_id, name),
        )
    for court_id, court_name, district_id in COURTS:
        cur.execute(
            "INSERT INTO court (court_id, court_name, district_id, state_id, active) "
            "VALUES (%s,%s,%s,1,TRUE) ON CONFLICT (court_id) DO NOTHING",
            (court_id, court_name, district_id),
        )
    for code, desc in [("323", "Punishment for voluntarily causing hurt"),
                       ("420", "Cheating and dishonestly inducing delivery of property"),
                       ("366", "Kidnapping")]:
        cur.execute(
            "INSERT INTO section (act_code, section_code, section_description, active) "
            "VALUES ('IPC',%s,%s,TRUE) ON CONFLICT (act_code, section_code) DO NOTHING",
            (code, desc),
        )

    # Officers: a couple per new station
    all_units = [(_d, u[0]) for _d, _n, units in DISTRICTS for u in units] + [
        (d, u) for d, u, *_r in EXISTING_UNITS
    ]
    employee_id = 2000
    unit_to_employees: dict[int, list[int]] = {}
    for district_id, unit_id in all_units:
        emp_ids = []
        for _ in range(2):
            employee_id += 1
            cur.execute(
                "INSERT INTO employee (employee_id, district_id, unit_id, rank_id, designation_id, kgid, first_name, gender_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,1) ON CONFLICT (employee_id) DO NOTHING",
                (employee_id, district_id, unit_id, random.choice([3, 4]), 1, f"KG{employee_id}", random_name()),
            )
            emp_ids.append(employee_id)
        unit_to_employees[unit_id] = emp_ids

    # --- Cases spread over ~15 months for trend charts ---
    all_station_choices = [(unit_id, lat, lon) for _d, _n, units in DISTRICTS for unit_id, _sn, lat, lon in units]
    all_station_choices += [(u, lat, lon) for _d, u, _sn, lat, lon in EXISTING_UNITS]
    all_courts = [c[0] for c in COURTS] + [1, 2]

    start_date = datetime(2025, 1, 1)
    end_date = datetime(2026, 4, 1)

    case_master_id = 100
    accused_master_id = 100
    victim_master_id = 100
    complainant_id = 100

    # Reserve a pool of case indices where repeat offenders will appear (2-4 cases each)
    n_cases = 70
    repeat_offender_case_slots = {name: random.sample(range(n_cases), k=random.randint(2, 4)) for name in REPEAT_OFFENDERS}

    for i in range(n_cases):
        case_master_id += 1
        unit_id, lat, lon = random.choice(all_station_choices)
        district_id = next((d for d, u in all_units if u == unit_id), 2)
        crime_sub_head_id = random.choice(ALL_CRIME_SUB_HEADS)
        crime_head_id = 1 if crime_sub_head_id in (10, 11, 30, 31) else 2
        case_status_id = random.choice(CASE_STATUS_IDS)
        gravity_id = random.choice(GRAVITY_IDS)
        category_id = random.choice(CASE_CATEGORY_IDS)
        court_id = random.choice(all_courts)
        reg_date = random_date(start_date, end_date)
        officer_pool = unit_to_employees.get(unit_id) or [1001]
        police_person_id = random.choice(officer_pool)
        g_lat, g_lon = jitter(lat, lon)

        crime_no = f"1{district_id:04d}{unit_id:04d}{reg_date.year}{case_master_id:05d}"
        case_no = f"{reg_date.year}{case_master_id:05d}"
        brief = BRIEF_FACTS_TEMPLATES[crime_sub_head_id].format(loc="the local market")

        cur.execute(
            """INSERT INTO case_master (
                case_master_id, crime_no, case_no, crime_registered_date, police_person_id,
                police_station_id, case_category_id, gravity_offence_id, crime_major_head_id,
                crime_minor_head_id, case_status_id, court_id, incident_from_date, incident_to_date,
                latitude, longitude, brief_facts
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (case_master_id) DO NOTHING""",
            (
                case_master_id, crime_no, case_no, reg_date.date(), police_person_id,
                unit_id, category_id, gravity_id, crime_head_id, crime_sub_head_id,
                case_status_id, court_id, reg_date, reg_date + timedelta(hours=1),
                g_lat, g_lon, brief,
            ),
        )

        act_code, section_code = random.choice(ACT_SECTIONS)
        cur.execute(
            "INSERT INTO act_section_association (case_master_id, act_id, section_id, act_order_id, section_order_id) "
            "VALUES (%s,%s,%s,1,1) ON CONFLICT DO NOTHING",
            (case_master_id, act_code, section_code),
        )

        # Victim + complainant
        victim_master_id += 1
        victim_name = random_name()
        cur.execute(
            "INSERT INTO victim (victim_master_id, case_master_id, victim_name, age_year, gender_id, victim_police) "
            "VALUES (%s,%s,%s,%s,%s,FALSE) ON CONFLICT (victim_master_id) DO NOTHING",
            (victim_master_id, case_master_id, victim_name, random.randint(18, 65), random.choice([1, 2])),
        )
        complainant_id += 1
        cur.execute(
            "INSERT INTO complainant_details (complainant_id, case_master_id, complainant_name, age_year, occupation_id, religion_id, caste_id, gender_id) "
            "VALUES (%s,%s,%s,%s,%s,1,1,%s) ON CONFLICT (complainant_id) DO NOTHING",
            (complainant_id, case_master_id, victim_name, random.randint(18, 65), random.choice([1, 2, 3, 4]), random.choice([1, 2])),
        )

        # Accused: 1-2 per case, occasionally a repeat offender
        n_accused = random.choice([1, 1, 2])
        for a in range(n_accused):
            accused_master_id += 1
            forced_name = next((name for name, slots in repeat_offender_case_slots.items() if i in slots), None)
            name = forced_name if (forced_name and a == 0) else random_name()
            cur.execute(
                "INSERT INTO accused (accused_master_id, case_master_id, accused_name, age_year, gender_id, person_id) "
                "VALUES (%s,%s,%s,%s,1,%s) ON CONFLICT (accused_master_id) DO NOTHING",
                (accused_master_id, case_master_id, name, random.randint(19, 50), f"A{a+1}"),
            )

    conn.commit()
    cur.execute("SELECT COUNT(*) FROM case_master")
    total_cases = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM accused")
    total_accused = cur.fetchone()[0]
    conn.close()
    print(f"Done. case_master now has {total_cases} rows, accused has {total_accused} rows.")
    print("Repeat offenders planted:", {k: len(v) for k, v in repeat_offender_case_slots.items()})


if __name__ == "__main__":
    main()
