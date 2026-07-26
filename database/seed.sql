-- Demo/lookup seed data for Crime AI (KSP Hackathon)
-- Enough rows to make the chatbot demo (Hubballi robbery cases, etc.) work end to end.

BEGIN;

INSERT INTO state (state_id, state_name, active) VALUES
    (1, 'Karnataka', TRUE);

INSERT INTO district (district_id, district_name, state_id, active) VALUES
    (1, 'Bengaluru Urban', 1, TRUE),
    (2, 'Dharwad', 1, TRUE),
    (3, 'Mysuru', 1, TRUE),
    (4, 'Ballari', 1, TRUE);

INSERT INTO unit_type (unit_type_id, unit_type_name, city_dist_state, hierarchy, active) VALUES
    (1, 'Police Station', 'City', 3, TRUE),
    (2, 'Circle Office', 'District', 2, TRUE),
    (3, 'District SP Office', 'District', 1, TRUE);

INSERT INTO unit (unit_id, unit_name, type_id, parent_unit, state_id, district_id, active) VALUES
    (100, 'Hubballi Rural PS', 1, NULL, 1, 2, TRUE),
    (101, 'Hubballi City PS', 1, NULL, 1, 2, TRUE),
    (102, 'Cubbon Park PS', 1, NULL, 1, 1, TRUE),
    (103, 'Mysuru West PS', 1, NULL, 1, 3, TRUE);

INSERT INTO rank_master (rank_id, rank_name, hierarchy, active) VALUES
    (1, 'Constable', 6, TRUE),
    (2, 'Head Constable', 5, TRUE),
    (3, 'Sub-Inspector', 4, TRUE),
    (4, 'Inspector', 3, TRUE),
    (5, 'DSP', 2, TRUE);

INSERT INTO designation (designation_id, designation_name, active, sort_order) VALUES
    (1, 'Investigating Officer', TRUE, 1),
    (2, 'SHO', TRUE, 2),
    (3, 'Analyst', TRUE, 3);

INSERT INTO case_category (case_category_id, lookup_value) VALUES
    (1, 'FIR'), (2, 'UDR'), (3, 'Zero FIR'), (4, 'PAR');

INSERT INTO gravity_offence (gravity_offence_id, lookup_value) VALUES
    (1, 'Heinous'), (2, 'Non-Heinous');

INSERT INTO crime_head (crime_head_id, crime_group_name, active) VALUES
    (1, 'Crimes Against Body', TRUE),
    (2, 'Crimes Against Property', TRUE);

INSERT INTO crime_sub_head (crime_sub_head_id, crime_head_id, crime_head_name, seq_id) VALUES
    (10, 1, 'Murder', 1),
    (11, 1, 'Assault', 2),
    (20, 2, 'Robbery', 1),
    (21, 2, 'Theft', 2);

INSERT INTO act (act_code, act_description, short_name, active) VALUES
    ('IPC', 'Indian Penal Code', 'IPC', TRUE);

INSERT INTO section (act_code, section_code, section_description, active) VALUES
    ('IPC', '392', 'Punishment for robbery', TRUE),
    ('IPC', '302', 'Punishment for murder', TRUE),
    ('IPC', '379', 'Punishment for theft', TRUE);

INSERT INTO case_status_master (case_status_id, case_status_name) VALUES
    (1, 'Under Investigation'),
    (2, 'Charge Sheeted'),
    (3, 'Closed');

INSERT INTO caste_master (caste_master_id, caste_master_name) VALUES (1, 'Not Disclosed');
INSERT INTO religion_master (religion_id, religion_name) VALUES (1, 'Not Disclosed');
INSERT INTO occupation_master (occupation_id, occupation_name) VALUES
    (1, 'Farmer'), (2, 'Government Employee'), (3, 'Private Employee'), (4, 'Unemployed');

INSERT INTO court (court_id, court_name, district_id, state_id, active) VALUES
    (1, 'District & Sessions Court, Dharwad', 2, 1, TRUE),
    (2, 'City Civil Court, Bengaluru', 1, 1, TRUE);

INSERT INTO employee (employee_id, district_id, unit_id, rank_id, designation_id, kgid, first_name, gender_id) VALUES
    (1001, 2, 100, 4, 1, 'KG1001', 'Ramesh Kulkarni', 1),
    (1002, 2, 101, 3, 2, 'KG1002', 'Suresh Patil', 1),
    (1003, 1, 102, 4, 1, 'KG1003', 'Anitha Rao', 2);

-- Sample FIRs, enough for a "robbery cases in Hubballi" / "pending ones" demo
INSERT INTO case_master (
    case_master_id, crime_no, case_no, crime_registered_date, police_person_id,
    police_station_id, case_category_id, gravity_offence_id, crime_major_head_id,
    crime_minor_head_id, case_status_id, court_id, incident_from_date, incident_to_date,
    latitude, longitude, brief_facts
) VALUES
    (1, '104430006202600001', '202600001', '2026-02-10', 1001, 100, 1, 1, 2, 20, 1, 1,
     '2026-02-09 22:00', '2026-02-09 23:30', 15.3647, 75.1240,
     'Complainant robbed at knifepoint near Hubballi railway station.'),
    (2, '104430006202600002', '202600002', '2026-03-05', 1002, 101, 1, 1, 2, 20, 1, 1,
     '2026-03-05 01:00', '2026-03-05 01:45', 15.3550, 75.1330,
     'Two unidentified men snatched a gold chain and fled on a motorcycle.'),
    (3, '104430006202600003', '202600003', '2026-01-20', 1001, 100, 1, 2, 2, 21, 2, 1,
     '2026-01-19 14:00', '2026-01-19 14:20', 15.3600, 75.1200,
     'Mobile phone stolen from a shop counter.'),
    (4, '104430001202600004', '202600004', '2026-04-02', 1003, 102, 1, 1, 1, 10, 1, 2,
     '2026-04-01 20:00', '2026-04-01 21:00', 12.9760, 77.5920,
     'Victim found with stab wounds following an altercation.');

INSERT INTO accused (accused_master_id, case_master_id, accused_name, age_year, gender_id, person_id) VALUES
    (1, 1, 'Suspect A', 27, 1, 'A1'),
    (2, 1, 'Suspect B', 24, 1, 'A2'),
    (3, 2, 'Suspect A', 27, 1, 'A1'),  -- same accused reappears on a second case -> network signal
    (4, 4, 'Suspect C', 31, 1, 'A1');

INSERT INTO victim (victim_master_id, case_master_id, victim_name, age_year, gender_id, victim_police) VALUES
    (1, 1, 'Victim 1', 45, 1, FALSE),
    (2, 2, 'Victim 2', 38, 2, FALSE),
    (3, 3, 'Victim 3', 29, 1, FALSE),
    (4, 4, 'Victim 4', 33, 1, FALSE);

INSERT INTO complainant_details (complainant_id, case_master_id, complainant_name, age_year, occupation_id, religion_id, caste_id, gender_id) VALUES
    (1, 1, 'Victim 1', 45, 3, 1, 1, 1),
    (2, 2, 'Victim 2', 38, 4, 1, 1, 2),
    (3, 3, 'Shop Owner', 50, 3, 1, 1, 1),
    (4, 4, 'Victim 4 Relative', 40, 1, 1, 1, 1);

INSERT INTO act_section_association (case_master_id, act_id, section_id, act_order_id, section_order_id) VALUES
    (1, 'IPC', '392', 1, 1),
    (2, 'IPC', '392', 1, 1),
    (3, 'IPC', '379', 1, 1),
    (4, 'IPC', '302', 1, 1);

COMMIT;
