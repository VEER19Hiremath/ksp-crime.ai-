
Usage (from backend/, venv active — loads DATABASE_URL / NEO4J_* from backend/.env):
    python ../neo4j/load_from_postgres.py
import sys
# Allow `python ../neo4j/load_from_postgres.py` from backend/ with .env loaded.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from core.config import get_settings  # noqa: E402
    settings = get_settings()
    database_url = settings.database_url or os.environ.get("DATABASE_URL")
    neo4j_uri = settings.neo4j_uri or os.environ.get("NEO4J_URI")
    neo4j_user = settings.neo4j_user or os.environ.get("NEO4J_USER", "neo4j")
    neo4j_password = settings.neo4j_password or os.environ.get("NEO4J_PASSWORD")
    if not database_url or not neo4j_uri or not neo4j_password:
        raise SystemExit("DATABASE_URL, NEO4J_URI, and NEO4J_PASSWORD must be set in backend/.env")

    with psycopg.connect(database_url) as pg_conn:
            SELECT cm.case_master_id, cm.crime_no, cm.case_no, cm.case_status_id,
                   cm.police_station_id, cm.court_id, cm.brief_facts,
                   u.unit_name, d.district_name,
                   csh.crime_head_name, ch.crime_group_name,
                   csm.case_status_name
            FROM case_master cm
            JOIN unit u ON u.unit_id = cm.police_station_id
            LEFT JOIN district d ON d.district_id = u.district_id
            JOIN crime_sub_head csh ON csh.crime_sub_head_id = cm.crime_minor_head_id
            LEFT JOIN crime_head ch ON ch.crime_head_id = csh.crime_head_id
            LEFT JOIN case_status_master csm ON csm.case_status_id = cm.case_status_id
    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))
            SET c.crime_no = $crime_no,
                c.case_no = $case_no,
                c.case_status_id = $case_status_id,
                c.brief_facts = $brief_facts,
                c.crime_head_name = $crime_head_name,
                c.crime_group_name = $crime_group_name,
                c.case_status_name = $case_status_name,
                c.unit_name = $unit_name,
                c.district_name = $district_name
            SET u.name = $unit_name, u.district_name = $district_name