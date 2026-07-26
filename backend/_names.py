from core.config import get_settings
from core.db import reset_pool, run_read_only_query

get_settings()
reset_pool()
rows = run_read_only_query(
    """
    SELECT a.accused_name, COUNT(DISTINCT a.case_master_id) AS n
    FROM accused a
    GROUP BY a.accused_name
    HAVING COUNT(DISTINCT a.case_master_id) >= 2
    ORDER BY n DESC
    LIMIT 8
    """
)
for r in rows:
    print(r["n"], r["accused_name"])
