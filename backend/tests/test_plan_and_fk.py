"""Tests for the optional driver capabilities: execution plan and foreign keys.

The plan parser is the interesting one — SHOWPLAN_XML is namespaced, deeply
nested and every attribute is optional, so it is easy to write a parser that
works on one plan and returns nothing useful on the next.

Runnable with pytest or directly:  python tests/test_plan_and_fk.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.drivers import get_driver  # noqa: E402

MSSQL = get_driver("mssql")

# A trimmed but structurally faithful SHOWPLAN_XML: namespaced root, nested
# RelOps, a missing-index recommendation and a warning.
SHOWPLAN = """<?xml version="1.0"?>
<ShowPlanXML xmlns="http://schemas.microsoft.com/sqlserver/2004/07/showplan">
  <BatchSequence><Batch><Statements>
    <StmtSimple StatementText="SELECT ...">
      <QueryPlan>
        <MissingIndexes>
          <MissingIndexGroup Impact="87.3">
            <MissingIndex Database="[Sites]" Schema="[dbo]" Table="[RevenueDaily]">
              <ColumnGroup Usage="EQUALITY">
                <Column Name="[SiteId]" ColumnId="2"/>
                <Column Name="[Date]" ColumnId="3"/>
              </ColumnGroup>
            </MissingIndex>
          </MissingIndexGroup>
        </MissingIndexes>
        <RelOp PhysicalOp="Hash Match" LogicalOp="Inner Join"
               EstimatedTotalSubtreeCost="41.2" EstimateIO="1.5" EstimateCPU="0.8"
               EstimateRows="1284">
          <Hash>
            <RelOp PhysicalOp="Clustered Index Scan" LogicalOp="Index Scan"
                   EstimatedTotalSubtreeCost="38.9" EstimateIO="30.0" EstimateCPU="8.0"
                   EstimateRows="41200000">
              <IndexScan>
                <Warnings>
                  <PlanAffectingConvert ConvertIssue="Seek Plan"
                                        Expression="CONVERT(int,[SiteId])"/>
                </Warnings>
              </IndexScan>
            </RelOp>
            <RelOp PhysicalOp="Index Seek" LogicalOp="Index Seek"
                   EstimatedTotalSubtreeCost="0.9" EstimateIO="0.6" EstimateCPU="0.3"
                   EstimateRows="1284"/>
          </Hash>
        </RelOp>
      </QueryPlan>
    </StmtSimple>
  </Statements></Batch></BatchSequence>
</ShowPlanXML>
"""


# ── capability probe ────────────────────────────────────────────────────────


def test_mssql_declares_both_capabilities():
    assert MSSQL.supports("execution_plan") is True
    assert MSSQL.supports("foreign_keys") is True


def test_other_dialects_report_unsupported():
    # The whole point of the probe: these must say no rather than half-work.
    for dialect in ("postgres", "mysql", "snowflake"):
        d = get_driver(dialect)
        assert d.supports("execution_plan") is False, dialect
        assert d.supports("foreign_keys") is False, dialect
        assert d.explain_statements("SELECT 1") is None, dialect
        assert d.foreign_keys_sql("public", "t") is None, dialect


def test_unknown_capability_is_false_not_an_error():
    assert MSSQL.supports("time_travel") is False


# ── plan parsing ────────────────────────────────────────────────────────────


def test_explain_statements_do_not_run_the_query():
    pre, stmt, post = MSSQL.explain_statements("SELECT 1")
    # SHOWPLAN_XML ON makes the server return the plan instead of executing —
    # that is what makes this safe against production.
    assert pre == "SET SHOWPLAN_XML ON"
    assert stmt == "SELECT 1"
    assert post == "SET SHOWPLAN_XML OFF"


def test_parse_plan_flattens_the_operator_tree():
    plan = MSSQL.parse_plan(SHOWPLAN)
    assert plan is not None
    ops = [(n["depth"], n["operator"]) for n in plan["nodes"]]
    assert ops == [
        (0, "Hash Match"),
        (1, "Clustered Index Scan"),
        (1, "Index Seek"),
    ]


def test_cost_share_uses_own_cost_not_cumulative_subtree_cost():
    # Regression guard. EstimatedTotalSubtreeCost is CUMULATIVE, so scoring on
    # it makes the root operator 100% on every plan and the "most expensive
    # operator" highlight always lands on the root — useless. Per-operator cost
    # is EstimateIO + EstimateCPU, which is what SSMS shows as "Cost: N%".
    plan = MSSQL.parse_plan(SHOWPLAN)
    by_op = {n["operator"]: n for n in plan["nodes"]}

    # Total own cost = (1.5+0.8) + (30+8) + (0.6+0.3) = 41.2
    assert by_op["Clustered Index Scan"]["cost_pct"] == 92.2
    assert by_op["Hash Match"]["cost_pct"] == 5.6
    assert by_op["Index Seek"]["cost_pct"] == 2.2

    # The scan, not the root, must be the dominant operator.
    dominant = max(plan["nodes"], key=lambda n: n["cost_pct"])
    assert dominant["operator"] == "Clustered Index Scan"
    assert sum(n["cost_pct"] for n in plan["nodes"]) == 100.0


def test_parse_plan_surfaces_row_estimates():
    plan = MSSQL.parse_plan(SHOWPLAN)
    scan = next(n for n in plan["nodes"] if n["operator"] == "Clustered Index Scan")
    assert scan["rows"] == 41200000


def test_parse_plan_extracts_missing_index():
    plan = MSSQL.parse_plan(SHOWPLAN)
    assert len(plan["missing_indexes"]) == 1
    mi = plan["missing_indexes"][0]
    assert mi["table"] == "[RevenueDaily]"
    assert mi["impact"] == 87.3
    assert mi["columns"] == ["[SiteId]", "[Date]"]


def test_parse_plan_extracts_warnings():
    plan = MSSQL.parse_plan(SHOWPLAN)
    assert any("Implicit conversion" in w for w in plan["warnings"])


def test_parse_plan_handles_garbage():
    # A plan that failed to come back must not take the request down with it.
    assert MSSQL.parse_plan("") is None
    assert MSSQL.parse_plan("not xml at all") is None
    assert MSSQL.parse_plan("<ShowPlanXML/>") is None


# ── foreign keys ────────────────────────────────────────────────────────────


def test_foreign_keys_sql_covers_both_directions():
    sql, params = MSSQL.foreign_keys_sql("dbo", "Sites")
    # Four params: the table as parent, and again as referenced. Without the
    # second arm the diagram would only show what a table points AT.
    assert params == ("dbo", "Sites", "dbo", "Sites")
    assert "sys.foreign_keys" in sql
    assert sql.count("?") == 4


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                passed += 1
                print(f"  PASS  {name}")
            except AssertionError as exc:
                failed += 1
                print(f"  FAIL  {name}: {exc}")
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
