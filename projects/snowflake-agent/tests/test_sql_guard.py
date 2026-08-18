import pytest

from snowflake_agent.sql_guard import SqlGuardError, validate_and_limit

MAX = 100


class TestAllowed:
    def test_plain_select_gets_limit(self):
        out = validate_and_limit("SELECT c_name FROM customer", MAX)
        assert "LIMIT 100" in out

    def test_existing_small_limit_is_kept(self):
        out = validate_and_limit("SELECT c_name FROM customer LIMIT 5", MAX)
        assert "LIMIT 5" in out

    def test_oversized_limit_is_capped(self):
        out = validate_and_limit("SELECT c_name FROM customer LIMIT 100000", MAX)
        assert "LIMIT 100" in out
        assert "100000" not in out

    def test_cte_is_allowed(self):
        sql = (
            "WITH top AS (SELECT c_custkey FROM customer) "
            "SELECT COUNT(*) FROM top"
        )
        out = validate_and_limit(sql, MAX)
        assert out.upper().startswith("WITH")
        assert "LIMIT 100" in out

    def test_union_is_allowed(self):
        out = validate_and_limit(
            "SELECT 1 AS x UNION ALL SELECT 2 AS x", MAX
        )
        assert "UNION ALL" in out.upper()
        assert "LIMIT 100" in out

    def test_join_group_by(self):
        sql = (
            "SELECT c.c_mktsegment, SUM(o.o_totalprice) AS total "
            "FROM orders o JOIN customer c ON o.o_custkey = c.c_custkey "
            "GROUP BY 1 ORDER BY total DESC"
        )
        out = validate_and_limit(sql, MAX)
        assert "GROUP BY" in out.upper()


class TestRejected:
    @pytest.mark.parametrize(
        "sql",
        [
            "INSERT INTO t VALUES (1)",
            "UPDATE t SET a = 1",
            "DELETE FROM t",
            "DROP TABLE t",
            "CREATE TABLE t (a INT)",
            "TRUNCATE TABLE t",
            "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET a = 1",
            "GRANT SELECT ON t TO ROLE r",
            "USE DATABASE d",
            "CALL my_proc()",
        ],
    )
    def test_non_select_statements(self, sql):
        with pytest.raises(SqlGuardError):
            validate_and_limit(sql, MAX)

    def test_multi_statement(self):
        with pytest.raises(SqlGuardError, match="複文"):
            validate_and_limit("SELECT 1; DROP TABLE t", MAX)

    def test_trailing_semicolon_is_ok(self):
        # セミコロン 1 個で終わるだけなら単文扱い
        out = validate_and_limit("SELECT 1;", MAX)
        assert "SELECT" in out.upper()

    def test_empty(self):
        with pytest.raises(SqlGuardError):
            validate_and_limit("   ", MAX)

    def test_unparsable(self):
        with pytest.raises(SqlGuardError):
            validate_and_limit("SELECT FROM WHERE", MAX)


class TestIdentifierGuard:
    def test_get_table_schema_rejects_injection(self):
        # tools 側の識別子バリデーションの回帰テスト
        from snowflake_agent.tools import _IDENTIFIER_RE

        assert _IDENTIFIER_RE.match("ORDERS")
        assert _IDENTIFIER_RE.match("line_item$1")
        assert not _IDENTIFIER_RE.match("ORDERS; DROP TABLE t")
        assert not _IDENTIFIER_RE.match("a-b")
        assert not _IDENTIFIER_RE.match("")
