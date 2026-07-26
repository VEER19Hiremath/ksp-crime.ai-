// Crime AI — Neo4j graph schema (Aura)
// Graph is derived FROM Postgres/Neon (see load_from_postgres.py); Neon stays the source of truth.

CREATE CONSTRAINT case_id IF NOT EXISTS FOR (c:Case) REQUIRE c.case_master_id IS UNIQUE;
CREATE CONSTRAINT accused_id IF NOT EXISTS FOR (a:Accused) REQUIRE a.accused_master_id IS UNIQUE;
CREATE CONSTRAINT victim_id IF NOT EXISTS FOR (v:Victim) REQUIRE v.victim_master_id IS UNIQUE;
CREATE CONSTRAINT officer_id IF NOT EXISTS FOR (e:Officer) REQUIRE e.employee_id IS UNIQUE;
CREATE CONSTRAINT court_id IF NOT EXISTS FOR (co:Court) REQUIRE co.court_id IS UNIQUE;
CREATE CONSTRAINT unit_id IF NOT EXISTS FOR (u:Unit) REQUIRE u.unit_id IS UNIQUE;

// Node labels: Case, Accused, Victim, Officer, Court, Unit
// Relationships:
//   (Accused)-[:INVOLVED_IN]->(Case)
//   (Victim)-[:VICTIM_OF]->(Case)
//   (Officer)-[:INVESTIGATED]->(Case)
//   (Case)-[:TRIED_AT]->(Court)
//   (Case)-[:REGISTERED_AT]->(Unit)
//   (Accused)-[:CO_ACCUSED_WITH {case_master_id}]->(Accused)   -- derived: co-occur on same case
//   (Accused)-[:SAME_PERSON_AS]->(Accused)                      -- name/attribute match across cases (fuzzy, reviewed)
