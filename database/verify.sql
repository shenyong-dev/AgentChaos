-- Read-only SQL checks after schema import and prepare-demo.
SELECT DATABASE() AS selected_database, VERSION() AS mysql_version;
SELECT version_num FROM alembic_version;
SELECT username, role, enabled, model_calls_enabled FROM ac_users ORDER BY id;
SELECT enabled AS agent_model_calls_enabled FROM ac_model_call_policy WHERE id = 1;
SELECT COUNT(*) AS login_events FROM ac_login_events;
SELECT adapter_type, COUNT(*) AS targets FROM ac_targets GROUP BY adapter_type;
SELECT case_key, dataset_split, review_status, source FROM ac_test_cases ORDER BY id;
SELECT fault_type, trigger_stage, target_tool FROM ac_fault_profiles ORDER BY id;
SELECT COUNT(*) AS runs_expected_zero FROM ac_test_runs;
SELECT COUNT(*) AS executions_expected_zero FROM ac_test_executions;
SELECT COUNT(*) AS bookings_expected_zero FROM sb_bookings;
