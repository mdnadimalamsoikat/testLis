-- ===========================================================================
-- fix_02 — ইনস্টল শেষ করে দেয় (ORA-00932-এ আটকে গেলে এটাই চালান)
--
-- APEX ইনস্টল থাকা বা না থাকার সাথে এই এররের কোনো সম্পর্ক নেই। সমস্যা আমার
-- SQL-এ ছিল: Oracle-এ  TIMESTAMP − TIMESTAMP = INTERVAL DAY TO SECOND,
-- NUMBER নয়। তাই  (SYSTIMESTAMP − last_seen_at) * 86400  →  ORA-00932।
--
-- এই ফাইলটা নিজেই সব শেষ করে দেয় — পুরোনো ফাইল বদলানোর দরকার নেই।
-- SQL Developer-এ TESTLIS ইউজার দিয়ে খুলে F5 (Run Script) চাপুন।
-- আবার চালালেও ক্ষতি নেই: ALTER কেবল না-থাকা কলামে, আর সিড হয়
-- WHERE NOT EXISTS দিয়ে।
--
-- যা করে:
--   ১. lis_analyzer_status-এর যে কলামগুলো নেই, শুধু সেগুলো যোগ করে
--   ২. চারটি v_lis_* ভিউ নতুন করে বানায় (INTERVAL-নিরাপদ হিসাব সহ)
--   ৩. দুটো ট্রিগার বসায় (config_version বাড়ানোর জন্য)
--   ৪. ৩ অ্যানালাইজার + ৫ সেটিং + ৪ কোড ম্যাপ সিড করে (থাকলে আর করে না)
--   ৫. শেষে দুটো কুয়েরি চালিয়ে দেখায় সব ঠিক আছে কি না
-- ===========================================================================

SET DEFINE OFF
SET SERVEROUTPUT ON

-- ১. হার্টবিট টেবিলের কলাম ------------------------------------------------
DECLARE
    PROCEDURE add_col(p_name VARCHAR2, p_type VARCHAR2) IS
        n NUMBER;
    BEGIN
        SELECT COUNT(*) INTO n
          FROM user_tab_columns
         WHERE table_name = 'LIS_ANALYZER_STATUS' AND column_name = p_name;
        IF n = 0 THEN
            EXECUTE IMMEDIATE 'ALTER TABLE lis_analyzer_status ADD (' || p_name || ' ' || p_type || ')';
            DBMS_OUTPUT.PUT_LINE('added   lis_analyzer_status.' || p_name);
        ELSE
            DBMS_OUTPUT.PUT_LINE('exists  lis_analyzer_status.' || p_name);
        END IF;
    END;
BEGIN
    add_col('HOST',               'VARCHAR2(120)');
    add_col('PORT',               'NUMBER');
    add_col('ACTIVE_CONNECTIONS', 'NUMBER DEFAULT 0');
    add_col('MESSAGES_RECEIVED',  'NUMBER DEFAULT 0');
    add_col('LAST_MESSAGE_AT',    'TIMESTAMP');
END;
/

-- ২. ভিউ (INTERVAL → সেকেন্ড; 19.3-এ এটাই নিরাপদ পথ) ----------------------
CREATE OR REPLACE VIEW v_lis_analyzers AS
SELECT c.config_id,
       c.analyzer_name,
       c.brand,
       c.model,
       c.enabled,
       c.status,
       c.protocol,
       c.connection_type,
       CASE WHEN c.connection_type = 'SERIAL'
            THEN NVL(c.serial_port, '-') || ' @ ' || c.baud_rate
            ELSE NVL(c.host, '-') || ':' || NVL(TO_CHAR(c.port), '-')
       END AS endpoint,
       c.test_codes,
       s.state,
       s.last_message_at,
       s.messages_received,
       CASE WHEN s.last_seen_at IS NULL THEN NULL
            ELSE ROUND(EXTRACT(DAY    FROM (CAST(SYSTIMESTAMP AS TIMESTAMP) - s.last_seen_at)) * 86400
                     + EXTRACT(HOUR   FROM (CAST(SYSTIMESTAMP AS TIMESTAMP) - s.last_seen_at)) * 3600
                     + EXTRACT(MINUTE FROM (CAST(SYSTIMESTAMP AS TIMESTAMP) - s.last_seen_at)) * 60
                     + EXTRACT(SECOND FROM (CAST(SYSTIMESTAMP AS TIMESTAMP) - s.last_seen_at)))
       END AS seconds_since_seen,
       c.last_applied_at,
       c.last_error,
       c.updated_at
  FROM lis_config c
  LEFT JOIN lis_analyzer_status s ON s.analyzer_name = c.analyzer_name
 WHERE c.config_type = 'ANALYZER'
/

CREATE OR REPLACE VIEW v_lis_settings AS
SELECT config_id,
       config_key,
       config_value,
       note,
       last_applied_at,
       last_error
  FROM lis_config
 WHERE config_type = 'SETTING'
/

CREATE OR REPLACE VIEW v_lis_health AS
SELECT COUNT(*)                                                        AS total_analyzers,
       SUM(CASE WHEN s.state = 'ONLINE' THEN 1 ELSE 0 END)             AS online_now,
       SUM(CASE WHEN c.last_error IS NOT NULL THEN 1 ELSE 0 END)       AS config_errors,
       (SELECT COUNT(*) FROM lis_commands WHERE status = 'PENDING')    AS pending_commands,
       (SELECT COUNT(*) FROM lab_results
         WHERE received_at >= TRUNC(SYSDATE))                          AS results_today,
       (SELECT COUNT(*) FROM lab_results
         WHERE is_critical = 'Y' AND received_at >= TRUNC(SYSDATE))    AS critical_today
  FROM lis_config c
  LEFT JOIN lis_analyzer_status s ON s.analyzer_name = c.analyzer_name
 WHERE c.config_type = 'ANALYZER'
   AND c.enabled = 'Y'
/

CREATE OR REPLACE VIEW v_lis_command_log AS
SELECT command_id, command_type, analyzer_name, payload, status,
       requested_by, requested_at, finished_at, result_message
  FROM lis_commands
 ORDER BY requested_at DESC
/

-- ৩. ট্রিগার ------------------------------------------------------------
CREATE OR REPLACE TRIGGER trg_lis_config_touch
BEFORE UPDATE ON lis_config
FOR EACH ROW
BEGIN
    -- শুধু আসল এডিটে version বাড়ে; সার্ভিস নিজের bookkeeping লিখলে নয়
    IF :NEW.analyzer_name  <> NVL(:OLD.analyzer_name, '~')
       OR :NEW.enabled          <> NVL(:OLD.enabled, '~')
       OR :NEW.status           <> NVL(:OLD.status, '~')
       OR NVL(:NEW.port, 0)     <> NVL(:OLD.port, 0)
       OR NVL(:NEW.protocol, '~') <> NVL(:OLD.protocol, '~')
       OR NVL(:NEW.connection_type, '~') <> NVL(:OLD.connection_type, '~')
       OR NVL(:NEW.serial_port, '~') <> NVL(:OLD.serial_port, '~')
       OR NVL(:NEW.baud_rate, 0) <> NVL(:OLD.baud_rate, 0)
       OR NVL(:NEW.parity, '~') <> NVL(:OLD.parity, '~')
       OR NVL(:NEW.test_code_map, '~') <> NVL(:OLD.test_code_map, '~')
       OR NVL(:NEW.config_value, '~') <> NVL(:OLD.config_value, '~') THEN
        :NEW.config_version := NVL(:OLD.config_version, 0) + 1;
        :NEW.updated_at     := SYSTIMESTAMP;
        :NEW.updated_by     := NVL(SYS_CONTEXT('USERENV', 'CLIENT_IDENTIFIER'),
                                   SYS_CONTEXT('USERENV', 'OS_USER'));
    END IF;
END;
/

CREATE OR REPLACE TRIGGER trg_lis_code_map_bump
AFTER INSERT OR UPDATE OR DELETE ON lis_test_code_map
BEGIN
    UPDATE lis_config
       SET config_version = config_version + 1
     WHERE config_type = 'ANALYZER';
END;
/

-- ৪. ডেমো ডেটা (আগেই থাকলে আর বসবে না) ---------------------------------

INSERT INTO lis_config (config_type, analyzer_name, display_order, enabled, protocol,
                        connection_type, host, port, brand, model, test_codes, note)
SELECT 'ANALYZER', 'Sysmex_XN_1000', 10, 'Y', 'ASTM', 'TCP', '0.0.0.0', 6661,
       'Sysmex', 'XN-1000', 'WBC,RBC,HGB,HCT,PLT', 'hematology'
  FROM dual
 WHERE NOT EXISTS (SELECT 1 FROM lis_config
                    WHERE config_type = 'ANALYZER' AND analyzer_name = 'Sysmex_XN_1000')
/

INSERT INTO lis_config (config_type, analyzer_name, display_order, enabled, protocol,
                        connection_type, host, port, brand, model, test_codes, note)
SELECT 'ANALYZER', 'Mindray_BS240', 20, 'Y', 'HL7', 'TCP', '0.0.0.0', 6662,
       'Mindray', 'BS-240', 'GLU,CREA,ALT,AST,UREA', 'chemistry'
  FROM dual
 WHERE NOT EXISTS (SELECT 1 FROM lis_config
                    WHERE config_type = 'ANALYZER' AND analyzer_name = 'Mindray_BS240')
/

INSERT INTO lis_config (config_type, analyzer_name, display_order, enabled, protocol,
                        connection_type, host, port, serial_port, baud_rate, data_bits,
                        parity, stop_bits, brand, model, test_codes, note)
SELECT 'ANALYZER', 'Roche_Cobas_c311', 30, 'N', 'ASTM', 'SERIAL', NULL, NULL, 'COM3',
       9600, 7, 'EVEN', 1, 'Roche', 'Cobas c311', 'GLU,CREA,ALT,AST', 'serial RS-232'
  FROM dual
 WHERE NOT EXISTS (SELECT 1 FROM lis_config
                    WHERE config_type = 'ANALYZER' AND analyzer_name = 'Roche_Cobas_c311')
/

INSERT INTO lis_config (config_type, analyzer_name, config_key, config_value, note)
SELECT 'SETTING', 'ACK_MODE', 'ACK_MODE', 'ASTM', 'ASTM = 0x06, MLLP = full ACK message'
  FROM dual
 WHERE NOT EXISTS (SELECT 1 FROM lis_config
                    WHERE config_type = 'SETTING' AND config_key = 'ACK_MODE')
/

INSERT INTO lis_config (config_type, analyzer_name, config_key, config_value, note)
SELECT 'SETTING', 'DEFAULT_PROTOCOL', 'DEFAULT_PROTOCOL', 'AUTO', 'AUTO sniffs ASTM vs HL7'
  FROM dual
 WHERE NOT EXISTS (SELECT 1 FROM lis_config
                    WHERE config_type = 'SETTING' AND config_key = 'DEFAULT_PROTOCOL')
/

INSERT INTO lis_config (config_type, analyzer_name, config_key, config_value, note)
SELECT 'SETTING', 'STRICT_TEST_CODES', 'STRICT_TEST_CODES', 'N', 'Y = drop unknown codes'
  FROM dual
 WHERE NOT EXISTS (SELECT 1 FROM lis_config
                    WHERE config_type = 'SETTING' AND config_key = 'STRICT_TEST_CODES')
/

INSERT INTO lis_config (config_type, analyzer_name, config_key, config_value, note)
SELECT 'SETTING', 'LOG_LEVEL', 'LOG_LEVEL', 'INFO', 'DEBUG/INFO/WARNING/ERROR'
  FROM dual
 WHERE NOT EXISTS (SELECT 1 FROM lis_config
                    WHERE config_type = 'SETTING' AND config_key = 'LOG_LEVEL')
/

INSERT INTO lis_config (config_type, analyzer_name, config_key, config_value, note)
SELECT 'SETTING', 'IGNORE_SAMPLE_PREFIXES', 'IGNORE_SAMPLE_PREFIXES', 'QC,CTRL,CAL',
       'control samples are ACKed but not stored'
  FROM dual
 WHERE NOT EXISTS (SELECT 1 FROM lis_config
                    WHERE config_type = 'SETTING' AND config_key = 'IGNORE_SAMPLE_PREFIXES')
/

INSERT INTO lis_test_code_map (machine_code, lis_code, note)
SELECT 'HGB', 'HB', 'Sysmex to report' FROM dual
 WHERE NOT EXISTS (SELECT 1 FROM lis_test_code_map WHERE machine_code = 'HGB')
/

INSERT INTO lis_test_code_map (machine_code, lis_code, note)
SELECT 'GLU', 'FBS', 'chemistry to report' FROM dual
 WHERE NOT EXISTS (SELECT 1 FROM lis_test_code_map WHERE machine_code = 'GLU')
/

INSERT INTO lis_test_code_map (machine_code, lis_code, note)
SELECT 'ALT', 'SGPT', 'liver panel' FROM dual
 WHERE NOT EXISTS (SELECT 1 FROM lis_test_code_map WHERE machine_code = 'ALT')
/

INSERT INTO lis_test_code_map (machine_code, lis_code, note)
SELECT 'AST', 'SGOT', 'liver panel' FROM dual
 WHERE NOT EXISTS (SELECT 1 FROM lis_test_code_map WHERE machine_code = 'AST')
/

COMMIT;

-- ৫. যাচাই — এই দুটো কুয়েরির ফল দেখেই বুঝবেন সব হয়ে গেছে ------------------
COLUMN analyzer_name FORMAT A22
COLUMN endpoint      FORMAT A22
COLUMN state         FORMAT A9

SELECT analyzer_name, protocol, endpoint, state, messages_received
  FROM v_lis_analyzers
 ORDER BY config_id;

SELECT COUNT(*) AS views_created
  FROM user_views
 WHERE view_name IN ('V_LIS_ANALYZERS', 'V_LIS_SETTINGS', 'V_LIS_HEALTH', 'V_LIS_COMMAND_LOG');

PROMPT
PROMPT fix_02 done.  Next:  python tools\apex_setup.py --verify-only --user TESTLIS --service ORCL
PROMPT
