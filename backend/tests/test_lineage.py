"""Tests for the lineage knowledge base.

The fixture graph is built from real node/edge shapes taken out of
`RMTools.dbo.LineageSnapshot`, including the awkward ones: labels that are
sometimes fully qualified (`stortrack.dbo.all_comp_sf_dist`), sometimes
schema-qualified (`dbo.pipeline_brief_state`), sometimes bare (`campaign`), and
an Aurora gold node whose database is `aurora` while the connection calls the
schema `gold`.
"""

import json
import pytest

from app.services import lineage
from app.services.drivers import ConnHandle


def _node(nid, label, db, schema, ntype="storage_raw", **extra):
    node = {
        "id": nid,
        "label": label,
        "type": ntype,
        "database": db,
        "schema": schema,
        "domain": None,
        "functions": [],
        "lifecycle": "active",
        "owner": "",
        "rail": 2,
        "isSpine": False,
        "fanIn": 0,
        "fanOut": 0,
        "provenance": "unreferenced",
        "databases_seen": [db] if db else [],
        "isOrphan": False,
    }
    node.update(extra)
    return node


GRAPH = {
    "nodes": [
        # Maintained: a DAG writes it, five things read it.
        _node(
            "tbl_unit_group_summary", "dbo.unit_group_summary", "se", "dbo",
            ntype="storage_aggregated", provenance="derived", fanIn=1, fanOut=5,
            domain="operations", functions=["operations"], existsInCatalog=True,
        ),
        # Aurora gold, fully-qualified-ish label, different database naming.
        _node(
            "tbl_gold_fact_occupied_units", "gold.fact_occupied_units", "aurora", "gold",
            ntype="storage_aggregated", provenance="derived", owner="gold-loader",
            fanIn=1, fanOut=5, existsInCatalog=True,
        ),
        # Nothing writes it.
        _node(
            "tbl_occupancy_old", "dbo.occupancy_old", "sites", "dbo",
            provenance="no_writer", existsInCatalog=True,
        ),
        # Retired by name pattern.
        _node(
            "tbl_occ_snapshot_20250114", "ga.dbo.occ_snapshot_20250114", "ga", "dbo",
            provenance="unreferenced", lifecycle="dated_snapshot", existsInCatalog=True,
        ),
        # Referenced in code, not in the catalog, and something still reads it.
        _node(
            "tbl_marketing_analytics_daily", "dbo.marketing_analytics_daily", "ga", "dbo",
            provenance="no_writer", existsInCatalog=False, fanOut=1,
        ),
        # Ambiguous bare name: two databases each have `campaign`.
        _node("tbl_campaign_a", "campaign", "g5", "dbo"),
        _node("tbl_campaign_b", "campaign", "meta", "dbo"),
        # Unresolved database — the graph never worked out where it lives.
        _node("tbl_mystery", "mystery_thing", "unknown", "dbo"),
        # A real Sites table whose NAME contains dots. Splitting the label on the
        # last dot would index this as `2025`.
        _node(
            "tbl_am_list", "dbo.AM List 08.08.2025 - Site List - 08.06.2025",
            "sites", "dbo",
        ),
        {"id": "rep_hit-list", "label": "hit-list", "type": "report", "database": None,
         "schema": None, "domain": None, "functions": [], "lifecycle": "active",
         "owner": "", "rail": 4, "isSpine": False, "fanIn": 1, "fanOut": 0,
         "connection": "default", "connections": ["default"], "category": "", "folder": ""},
    ],
    "edges": [
        {"id": "e1", "source": "src_storedge", "target": "tbl_unit_group_summary",
         "type": "airflow_dag", "label": "closeOfDay.py", "owner": "Anam",
         "dag": "Anam_companyWide_closeOfDay_daily", "status": "active",
         "span": 1, "isSkip": False, "isFeedback": False},
        {"id": "e2", "source": "tbl_unit_group_summary", "target": "rep_hit-list",
         "type": "reporting_query", "label": "hit-list", "connection": "default",
         "status": "active", "span": 1, "isSkip": False, "isFeedback": False},
        {"id": "e3", "source": "tbl_gold_fact_occupied_units", "target": "rep_hit-list",
         "type": "reporting_query", "label": "hit-list", "connection": "default",
         "status": "active", "span": 1, "isSkip": False, "isFeedback": False},
        # A read of a table that no longer exists.
        {"id": "e4", "source": "tbl_marketing_analytics_daily", "target": "rep_hit-list",
         "type": "reporting_query", "label": "hit-list", "connection": "default",
         "status": "broken", "span": 3, "isSkip": True, "isFeedback": False},
    ],
}


# The real edge set for Stortrack.dbo.Rates, copied out of the live snapshot.
# One pipeline, six edges: an `airflow_dag` edge with dag=null and the DAG name
# in `label`, a second `airflow_dag` edge for a different DAG, and four
# byte-identical `transformation` edges (one per call site in the script).
RATES_GRAPH = {
    "nodes": [
        _node("tbl_rates", "Stortrack.dbo.Rates", "stortrack", "dbo",
              ntype="storage_aggregated", provenance="derived", fanIn=2, fanOut=9,
              existsInCatalog=True),
    ],
    "edges": (
        [{"id": "a1", "source": "src_stortrack", "target": "tbl_rates",
          "type": "airflow_dag", "label": "Anam_CPJ_StorTrack_test_DAG", "owner": "Anam",
          "status": "active", "span": 1, "isSkip": False, "isFeedback": False},
         {"id": "a2", "source": "src_s3", "target": "tbl_rates",
          "type": "airflow_dag", "label": "Anam_import_s3_rates", "owner": "Anam",
          "status": "active", "span": 1, "isSkip": False, "isFeedback": False}]
        + [{"id": f"t{i}", "source": "src_stortrack", "target": "tbl_rates",
            "type": "transformation", "label": "StorTrack_wDuplicateLogic.py",
            "dag": "Anam_CPJ_StorTrack_test_DAG", "owner": "Anam",
            "status": "active", "span": 1, "isSkip": False, "isFeedback": False}
           for i in range(4)]
    ),
}


def test_duplicate_writer_edges_collapse_to_one_pipeline():
    """Six edges, two real pipelines. Without collapsing, the summary claims six."""
    snap = lineage._Snapshot(RATES_GRAPH)
    facts = snap.lookup("Stortrack", "dbo", "Rates")
    assert len(facts.writers) == 2
    dags = {w["dag"] for w in facts.writers}
    assert dags == {"Anam_CPJ_StorTrack_test_DAG", "Anam_import_s3_rates"}


def test_airflow_dag_edge_names_the_dag_even_with_a_null_dag_field():
    """`airflow_dag` edges leave `dag` null and put the name in `label`."""
    snap = lineage._Snapshot(RATES_GRAPH)
    facts = snap.lookup("Stortrack", "dbo", "Rates")
    by_dag = {w["dag"]: w for w in facts.writers}
    assert by_dag["Anam_import_s3_rates"]["script"] == ""


def test_the_more_specific_writer_edge_wins():
    """The airflow_dag edge knows only the DAG; the transformation edge knows the
    DAG and its script. The merged writer should carry both."""
    snap = lineage._Snapshot(RATES_GRAPH)
    facts = snap.lookup("Stortrack", "dbo", "Rates")
    by_dag = {w["dag"]: w for w in facts.writers}
    assert by_dag["Anam_CPJ_StorTrack_test_DAG"]["script"] == "StorTrack_wDuplicateLogic.py"


def test_describe_does_not_claim_six_writers_for_one_pipeline():
    snap = lineage._Snapshot(RATES_GRAPH)
    text = lineage.describe(snap.lookup("Stortrack", "dbo", "Rates"))
    assert "more writers" not in text
    assert "Anam_CPJ_StorTrack_test_DAG (StorTrack_wDuplicateLogic.py)" in text


def test_describe_does_not_repeat_the_name_as_its_own_script():
    """When the DAG name came from `label` there is no separate script, and
    rendering `written by X (X)` reads like two different things."""
    snap = lineage._Snapshot(RATES_GRAPH)
    text = lineage.describe(snap.lookup("Stortrack", "dbo", "Rates"))
    assert "Anam_import_s3_rates (Anam_import_s3_rates)" not in text


@pytest.fixture
def snap():
    return lineage._Snapshot(GRAPH, "2026-08-05", "2026-08-05 17:00:40")


@pytest.fixture(autouse=True)
def _clear_cache():
    lineage.reset_cache()
    yield
    lineage.reset_cache()


# ── lookup ────────────────────────────────────────────────────────────────────

def test_exact_lookup_finds_the_writer_dag(snap):
    facts = snap.lookup("sE", "dbo", "unit_group_summary")
    assert facts is not None
    assert facts.is_maintained
    assert facts.writers[0]["dag"] == "Anam_companyWide_closeOfDay_daily"
    assert facts.writers[0]["script"] == "closeOfDay.py"


def test_lookup_is_case_insensitive(snap):
    assert snap.lookup("SE", "DBO", "UNIT_GROUP_SUMMARY") is not None


def test_aurora_gold_resolves_through_the_alias(snap):
    """The connection calls the database `gold`; the graph calls it `aurora`."""
    facts = snap.lookup("gold", "gold", "fact_occupied_units")
    assert facts is not None
    assert facts.owner == "gold-loader"
    assert facts.is_maintained


def test_fully_qualified_label_still_yields_the_bare_table_name(snap):
    """`ga.dbo.occ_snapshot_20250114` must index as `occ_snapshot_20250114`."""
    facts = snap.lookup("ga", "dbo", "occ_snapshot_20250114")
    assert facts is not None
    assert facts.is_retired


def test_ambiguous_bare_name_is_refused_rather_than_guessed(snap):
    """Two databases hold `campaign`. Attributing one's pipeline to the other
    would be worse than returning nothing."""
    assert snap.lookup("nowhere", "dbo", "campaign") is None
    # But naming the database resolves it.
    assert snap.lookup("g5", "dbo", "campaign") is not None


def test_unresolved_database_node_is_still_findable_by_unique_name(snap):
    assert snap.lookup("whatever", "dbo", "mystery_thing") is not None


def test_unknown_table_returns_none(snap):
    assert snap.lookup("sE", "dbo", "no_such_table") is None


def test_a_table_name_containing_dots_still_resolves(snap):
    """Sites.dbo.[AM List 08.08.2025 - Site List - 08.06.2025] is a real table.
    Taking the last dot-segment of the label would index it as `2025`."""
    assert snap.lookup("sites", "dbo", "AM List 08.08.2025 - Site List - 08.06.2025") is not None


def test_reporting_edge_is_a_read_not_a_write(snap):
    """A table -> report edge must never make the report a writer of anything,
    nor the table a writer of the report."""
    facts = snap.lookup("sE", "dbo", "unit_group_summary")
    assert [w["dag"] for w in facts.writers] == ["Anam_companyWide_closeOfDay_daily"]
    assert "hit-list" in facts.readers


# ── classification ────────────────────────────────────────────────────────────

def test_no_writer_is_not_maintained(snap):
    facts = snap.lookup("sites", "dbo", "occupancy_old")
    assert not facts.is_maintained
    assert not facts.is_retired


def test_missing_from_catalog_is_flagged(snap):
    facts = snap.lookup("ga", "dbo", "marketing_analytics_daily")
    assert facts.is_missing
    assert facts.has_broken_reads


def test_maintained_outranks_unmaintained(snap):
    good = snap.lookup("sE", "dbo", "unit_group_summary")
    bad = snap.lookup("sites", "dbo", "occupancy_old")
    assert good.rank() < bad.rank()


def test_retired_outranks_nothing_but_sorts_after_live(snap):
    live = snap.lookup("sites", "dbo", "occupancy_old")
    retired = snap.lookup("ga", "dbo", "occ_snapshot_20250114")
    assert live.rank() < retired.rank()


def test_more_read_tables_outrank_less_read_ones():
    a = lineage.TableFacts(provenance="derived", fan_out=9)
    b = lineage.TableFacts(provenance="derived", fan_out=1)
    assert a.rank() < b.rank()


# ── describe ──────────────────────────────────────────────────────────────────

def test_describe_names_the_dag(snap):
    text = lineage.describe(snap.lookup("sE", "dbo", "unit_group_summary"))
    assert "Anam_companyWide_closeOfDay_daily" in text
    assert "closeOfDay.py" in text
    assert "read by 5" in text


def test_describe_calls_out_an_unmaintained_table(snap):
    text = lineage.describe(snap.lookup("sites", "dbo", "occupancy_old"))
    assert "NO WRITER" in text


def test_describe_calls_out_a_missing_table(snap):
    text = lineage.describe(snap.lookup("ga", "dbo", "marketing_analytics_daily"))
    assert "MISSING FROM CATALOG" in text
    assert "failing" in text


def test_describe_handles_no_record():
    assert lineage.describe(None) == "no lineage record"


def test_describe_does_not_leak_the_provenance_enum():
    """A derived table whose writer edge didn't resolve still has to read as a
    sentence, not as `provenance derived`."""
    text = lineage.describe(lineage.TableFacts(provenance="derived", fan_out=1))
    assert "provenance" not in text
    assert "built by a pipeline" in text


def test_describe_distinguishes_no_write_seen_from_no_writer():
    """A maintained table with no observed write must not read as unmaintained —
    last_user_update is cleared by a server restart."""
    facts = lineage.TableFacts(provenance="derived", row_count=100)
    text = lineage.describe(facts)
    assert "NO WRITER" not in text
    assert "since the server last restarted" in text


def test_describe_reports_a_stale_maintained_table():
    facts = lineage.TableFacts(
        provenance="derived",
        writers=[{"dag": "Some_DAG", "script": "s.py", "owner": "x", "endpoint": "", "status": "active"}],
        row_count=4102,
        last_write="2026-02-01 03:00:00",
    )
    text = lineage.describe(facts)
    assert "Some_DAG" in text
    assert "4,102 rows" in text
    assert "last write 2026-02-01 03:00:00" in text


# ── suggestions ───────────────────────────────────────────────────────────────

def test_suggestions_name_a_maintained_table_from_the_connected_database(snap):
    out = lineage.build_suggestions(snap, "sE")
    assert "unit_group_summary" in out[0]["text"]
    assert out[0]["mode"] == "sql"


def test_suggestions_never_offer_an_unmaintained_table(snap):
    """`occupancy_old` and `occ_snapshot_20250114` must never be suggested —
    a suggestion is a promise the question will work."""
    for db in ("sites", "ga"):
        for s in lineage.build_suggestions(snap, db):
            assert "occupancy_old" not in s["text"]
            assert "occ_snapshot" not in s["text"]
            assert "marketing_analytics_daily" not in s["text"]


def test_suggestions_fall_back_when_the_database_has_nothing_maintained(snap):
    out = lineage.build_suggestions(snap, "sites")
    assert out[0]["text"] == lineage.GENERIC_SUGGESTIONS[0]["text"]


def test_suggestions_fall_back_with_no_snapshot_at_all():
    out = lineage.build_suggestions(None, "sE")
    assert [s["text"] for s in out[:2]] == [s["text"] for s in lineage.GENERIC_SUGGESTIONS]


def test_suggestions_always_include_exactly_one_find_mode_entry(snap):
    for db in ("sE", "sites", "gold", "nonexistent"):
        modes = [s["mode"] for s in lineage.build_suggestions(snap, db)]
        assert modes.count("find") == 1
        assert modes[-1] == "find"


def test_the_find_suggestion_names_the_database_domain(snap):
    """sE's maintained table is domain `operations`, so the find question should
    say operations rather than a hardcoded topic."""
    out = lineage.build_suggestions(snap, "sE")
    assert out[-1]["text"] == "Where does operations data live?"


def test_a_domain_that_restates_the_database_is_not_used():
    """The graph falls back to the database name when it can't classify a table:
    gold.dim_site really does carry domain `aurora`. "Where does aurora data
    live?" is not a question anyone asks — the next real domain wins."""
    graph = {
        "nodes": [
            _node("tbl_dim_site", "gold.dim_site", "aurora", "gold",
                  ntype="storage_aggregated", provenance="derived", fanOut=37,
                  domain="aurora", existsInCatalog=True),
            _node("tbl_dim_ug", "gold.dim_unit_group", "aurora", "gold",
                  ntype="storage_aggregated", provenance="derived", fanOut=16,
                  domain="operations", existsInCatalog=True),
        ],
        "edges": [],
    }
    out = lineage.build_suggestions(lineage._Snapshot(graph), "gold")
    assert out[-1]["text"] == "Where does operations data live?"


def test_a_shortened_database_domain_is_also_rejected():
    """`RV Sites` yields domain `rv` — a substring, not an exact match."""
    assert lineage._restates_database("rv", "RV Sites")
    assert lineage._restates_database("aurora", "gold")   # via the alias
    assert not lineage._restates_database("pricing", "sE")
    assert not lineage._restates_database("marketing", "Sites")


def test_suggestions_follow_the_aurora_alias(snap):
    out = lineage.build_suggestions(snap, "gold")
    assert "fact_occupied_units" in out[0]["text"]


def test_suggestions_preserve_the_real_table_casing():
    """`Market_Rates` reads better than `market_rates`, and the lookup index is
    lowercased, so casing has to be carried separately."""
    graph = {
        "nodes": [_node("tbl_mr", "StorTrack.dbo.Market_Rates", "stortrack", "dbo",
                        ntype="storage_aggregated", provenance="derived", fanOut=4,
                        existsInCatalog=True)],
        "edges": [],
    }
    out = lineage.build_suggestions(lineage._Snapshot(graph), "Stortrack")
    assert "Market_Rates" in out[0]["text"]


def test_a_non_dbo_schema_is_qualified_in_the_suggestion():
    graph = {
        "nodes": [_node("tbl_x", "analytics.conversions", "ga", "analytics",
                        ntype="storage_aggregated", provenance="derived", fanOut=2,
                        existsInCatalog=True)],
        "edges": [],
    }
    out = lineage.build_suggestions(lineage._Snapshot(graph), "ga")
    assert "analytics.conversions" in out[0]["text"]


# ── snapshot loading ──────────────────────────────────────────────────────────

def test_load_snapshot_caches_and_survives_failure(monkeypatch):
    calls = []

    def fake_execute(conn, sql, *args):
        calls.append(sql)
        return {"rows": [["2026-08-05", "2026-08-05 17:00:40", json.dumps(GRAPH)]],
                "columns": [], "error": None}

    monkeypatch.setattr(lineage, "execute_query", fake_execute)
    conn = ConnHandle("mssql", "dsn")
    first = lineage.load_snapshot(conn)
    second = lineage.load_snapshot(conn)
    assert first is second
    assert len(calls) == 1


def test_load_snapshot_returns_none_when_the_table_is_absent(monkeypatch):
    monkeypatch.setattr(
        lineage, "execute_query",
        lambda conn, sql, *a: {"rows": [], "columns": [], "error": "Invalid object name"},
    )
    assert lineage.load_snapshot(ConnHandle("mssql", "dsn")) is None


def test_a_failed_load_is_not_retried_on_every_question(monkeypatch):
    calls = []

    def failing(conn, sql, *args):
        calls.append(sql)
        return {"rows": [], "columns": [], "error": "Invalid object name"}

    monkeypatch.setattr(lineage, "execute_query", failing)
    conn = ConnHandle("mssql", "dsn")
    lineage.load_snapshot(conn)
    lineage.load_snapshot(conn)
    assert len(calls) == 1


def test_unparseable_graph_is_swallowed(monkeypatch):
    monkeypatch.setattr(
        lineage, "execute_query",
        lambda conn, sql, *a: {"rows": [["2026-08-05", "x", "{not json"]],
                               "columns": [], "error": None},
    )
    assert lineage.load_snapshot(ConnHandle("mssql", "dsn")) is None


# ── write stats ───────────────────────────────────────────────────────────────

def test_write_stats_are_skipped_for_non_mssql():
    assert lineage.fetch_write_stats(ConnHandle("postgres", "dsn")) == {}


def test_write_stats_degrade_to_empty_without_dmv_permission(monkeypatch):
    monkeypatch.setattr(
        lineage, "execute_query",
        lambda conn, sql, *a: {"rows": [], "columns": [], "error": "VIEW DATABASE STATE denied"},
    )
    assert lineage.fetch_write_stats(ConnHandle("mssql", "dsn")) == {}


def test_write_stats_are_keyed_lowercase(monkeypatch):
    monkeypatch.setattr(
        lineage, "execute_query",
        lambda conn, sql, *a: {
            "rows": [["dbo", "Unit_Group_Summary", 1284003, "2026-08-05 08:14:22.123"]],
            "columns": [], "error": None},
    )
    stats = lineage.fetch_write_stats(ConnHandle("mssql", "dsn"))
    assert stats[("dbo", "unit_group_summary")] == (1284003, "2026-08-05 08:14:22")
