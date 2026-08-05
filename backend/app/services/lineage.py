"""The data knowledge base: which tables a pipeline actually maintains.

`find_data` used to answer "where does occupancy live?" by running
`LIKE '%occupancy%'` against `INFORMATION_SCHEMA.COLUMNS` in every database and
handing every hit to Claude as an equally plausible candidate. Of roughly 580
tables across this server and Aurora gold, only about 150 have anything writing
them — the rest are backups, dated snapshots, one-off experiments and tables
whose producer was deleted years ago. Text similarity cannot tell those apart,
so the assistant confidently pointed people at dead tables.

This module supplies the missing half: what the pipelines actually do.

Two independent sources, deliberately kept separate because they fail
differently and disagree usefully:

1. **Code-derived lineage** — `RMTools.dbo.LineageSnapshot`, written daily by
   `Cole_LineageMap_DAG`. That DAG clones gitflow and wwg-reports, statically
   analyses every DAG, script and report, then reconciles the result against the
   real SQL Server and Aurora catalogs. It knows which DAG writes a table, who
   owns it, what reads it downstream, and whether the table still exists at all.
   It cannot know whether the DAG ran.

2. **Live write stats** — `sys.dm_db_partition_stats` and
   `sys.dm_db_index_usage_stats` on the server itself. These know whether rows
   moved, and nothing about why. They catch the case lineage structurally
   cannot: a table with a healthy, correct, currently-deployed DAG that quietly
   stopped writing three months ago.

Together they separate "nothing maintains this" from "something is supposed to
maintain this and has stopped", which are very different answers to give
someone. Either source may be unavailable — no snapshot yet, no DMV permission,
a server that isn't SQL Server — and every function here degrades to returning
nothing rather than raising, because a missing knowledge base should cost the
assistant its precision, not its ability to answer.
"""

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from app.services.connection import execute_query
from app.services.drivers import ConnHandle

logger = logging.getLogger(__name__)

SNAPSHOT_TABLE = "RMTools.dbo.LineageSnapshot"

# The snapshot is rebuilt once a day (17:00 UTC). Re-reading a 0.6 MB blob and
# re-indexing ~700 nodes on every question would be pure waste, so it is cached
# for an hour — long enough to cost nothing, short enough that a same-day
# rebuild is picked up without a service restart.
_CACHE_TTL_SECONDS = 3600

# Lineage names databases in lowercase, and models Aurora gold as the database
# `aurora` regardless of what the Postgres connection calls it. Both sides are
# lowercased before lookup; this maps what the *app* calls a database onto what
# the *graph* calls it. Add an entry only when the two genuinely disagree.
_DB_ALIASES: dict[str, str] = {
    "gold": "aurora",
    "postgres": "aurora",
}

# Lifecycle values that mean "this exists, but not as a thing you should query".
# `active` and `variant` are absent on purpose: a variant is a real table that
# happens to look like a sibling of another one.
_RETIRED_LIFECYCLES = frozenset({"backup", "deprecated", "dated_snapshot", "test"})


@dataclass
class TableFacts:
    """What the knowledge base knows about one table.

    Every field is optional-ish by design. A table can be in the catalog with no
    lineage node, in lineage with no catalog row, or known to both — and the
    renderer has to say something sensible in all three cases.
    """

    # ── from lineage ──────────────────────────────────────────────────────────
    provenance: Optional[str] = None      # ingested | derived | no_writer | unreferenced
    lifecycle: Optional[str] = None       # active | backup | deprecated | dated_snapshot | test | variant
    exists_in_catalog: Optional[bool] = None
    domain: Optional[str] = None
    functions: list[str] = field(default_factory=list)
    owner: Optional[str] = None
    fan_in: int = 0
    fan_out: int = 0
    # Direct writers: {"dag": "Anam_...", "script": "closeOfDay.py", "owner": "Anam"}
    writers: list[dict] = field(default_factory=list)
    # Labels of things that read this table (reports and downstream tables).
    readers: list[str] = field(default_factory=list)
    # A reader whose read is broken because this table no longer exists.
    has_broken_reads: bool = False

    # ── from the live server ──────────────────────────────────────────────────
    row_count: Optional[int] = None
    last_write: Optional[str] = None      # ISO-ish string, or None if unknown

    @property
    def is_maintained(self) -> bool:
        """True when a pipeline in either repo declares itself the writer.

        Note this is a statement about the CODE, not about whether the pipeline
        ran. `last_write` is the check for that, and the two are reported
        separately so a stalled pipeline doesn't masquerade as an absent one.
        """
        return self.provenance in ("ingested", "derived") or bool(self.writers)

    @property
    def is_retired(self) -> bool:
        return (self.lifecycle or "active") in _RETIRED_LIFECYCLES

    @property
    def is_missing(self) -> bool:
        """Referenced in code but absent from the catalog when lineage last looked."""
        return self.exists_in_catalog is False

    def rank(self) -> tuple:
        """Sort key, lowest first. Maintained and widely-read tables come first.

        Deliberately ranks rather than filters: an unreferenced table is
        sometimes genuinely the right answer (someone loads it by hand, or a
        script outside these two repos writes it), and hiding it would replace
        one kind of wrong answer with another.
        """
        return (
            0 if self.is_maintained else 1,
            1 if self.is_retired else 0,
            1 if self.is_missing else 0,
            -self.fan_out,
            -self.fan_in,
        )


class _Snapshot:
    """One parsed lineage graph, indexed for lookup."""

    def __init__(self, graph: dict, snapshot_date: str = "", captured_at: str = ""):
        self.snapshot_date = snapshot_date
        self.captured_at = captured_at
        self.node_count = len(graph.get("nodes") or [])
        self.edge_count = len(graph.get("edges") or [])

        nodes = graph.get("nodes") or []
        edges = graph.get("edges") or []

        self._by_id: dict[str, dict] = {}
        # (database, schema, table) -> node id, all lowercased.
        self._exact: dict[tuple[str, str, str], str] = {}
        # (database, table) -> node id, for a schema mismatch.
        self._by_db_table: dict[tuple[str, str], str] = {}
        # table -> [node id]. Only usable when it resolves to exactly one node.
        self._by_table: dict[str, list[str]] = {}

        for node in nodes:
            nid = node.get("id")
            if not nid:
                continue
            self._by_id[nid] = node
            if not str(node.get("type", "")).startswith("storage"):
                continue
            table = _table_name_of(node)
            if not table:
                continue
            db = _norm(node.get("database"))
            schema = _norm(node.get("schema"))
            if db and db != "unknown":
                self._exact.setdefault((db, schema, table), nid)
                self._by_db_table.setdefault((db, table), nid)
            self._by_table.setdefault(table, []).append(nid)

        # Writers and readers, resolved per node.
        self._writers: dict[str, list[dict]] = {}
        self._readers: dict[str, list[str]] = {}
        self._broken_target: set[str] = set()
        for edge in edges:
            src, tgt = edge.get("source"), edge.get("target")
            etype, status = edge.get("type"), edge.get("status")
            if not src or not tgt:
                continue
            # A reporting_query edge points table -> report: it is a READ of the
            # source, never a write of the target. Only the two pipeline edge
            # types write anything.
            if etype in ("airflow_dag", "transformation"):
                # The two write edge types name the pipeline differently. A
                # `transformation` edge carries the DAG in `dag` and the script
                # in `label`; an `airflow_dag` edge leaves `dag` null and puts
                # the DAG (or the ingest script) in `label`. Normalise both to
                # (pipeline, script) here so the renderer doesn't have to care.
                dag = edge.get("dag") or ""
                label = edge.get("label") or ""
                self._add_writer(
                    tgt,
                    {
                        "dag": dag or label,
                        "script": label if dag else "",
                        "owner": edge.get("owner") or "",
                        "endpoint": edge.get("endpoint") or "",
                        "status": status or "",
                    },
                )
                self._readers.setdefault(src, []).append(_label_of(self._by_id.get(tgt), tgt))
            elif etype == "reporting_query":
                self._readers.setdefault(src, []).append(_label_of(self._by_id.get(tgt), tgt))
            if status == "broken":
                self._broken_target.add(src)

    def _add_writer(self, target: str, writer: dict) -> None:
        """Record a writer, collapsing duplicates.

        The extractor emits one edge per call site, so a script that writes the
        same table from four places yields four identical edges — real data:
        `Stortrack.dbo.Rates` has four from `StorTrack_wDuplicateLogic.py` alone,
        plus a second `airflow_dag` edge naming the same DAG. Left as-is the
        summary reads "written by X; written by X; +4 more writers", which
        misrepresents one pipeline as six.
        """
        writers = self._writers.setdefault(target, [])
        key = (writer["dag"], writer["script"], writer["endpoint"])
        for existing in writers:
            if (existing["dag"], existing["script"], existing["endpoint"]) == key:
                return
            # An airflow_dag edge and a transformation edge often describe the
            # same pipeline, one naming the DAG and the other naming the DAG
            # plus its script. Keep the more specific of the two.
            if existing["dag"] == writer["dag"]:
                if writer["script"] and not existing["script"]:
                    existing["script"] = writer["script"]
                    existing["owner"] = existing["owner"] or writer["owner"]
                return
        writers.append(writer)

    def lookup(self, database: str, schema: str, table: str) -> Optional[TableFacts]:
        """Resolve a table to its facts, most specific match first.

        The bare-name fallback is deliberately refused when it is ambiguous.
        Two databases each holding a `campaign` table is common, and guessing
        which one the graph meant would attribute one table's pipeline to
        another — a worse failure than saying nothing.
        """
        db = _DB_ALIASES.get(_norm(database), _norm(database))
        schema_n = _norm(schema)
        table_n = _norm(table)

        nid = self._exact.get((db, schema_n, table_n)) or self._by_db_table.get((db, table_n))
        if nid is None:
            candidates = self._by_table.get(table_n) or []
            if len(candidates) != 1:
                return None
            nid = candidates[0]
        return self._facts_for(nid)

    def _facts_for(self, nid: str) -> Optional[TableFacts]:
        node = self._by_id.get(nid)
        if node is None:
            return None
        writers = self._writers.get(nid, [])
        readers = self._readers.get(nid, [])
        # Dedupe readers but keep insertion order — the first few are what gets
        # shown, and a stable order keeps the prompt cacheable.
        seen: list[str] = []
        for r in readers:
            if r not in seen:
                seen.append(r)
        return TableFacts(
            provenance=node.get("provenance"),
            lifecycle=node.get("lifecycle"),
            exists_in_catalog=node.get("existsInCatalog"),
            domain=node.get("domain"),
            functions=list(node.get("functions") or []),
            owner=node.get("owner") or None,
            fan_in=int(node.get("fanIn") or 0),
            fan_out=int(node.get("fanOut") or 0),
            writers=writers,
            readers=seen,
            has_broken_reads=nid in self._broken_target,
        )


def _norm(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _label_of(node: Optional[dict], fallback: str) -> str:
    if node and node.get("label"):
        return str(node["label"])
    return fallback


def _table_name_of(node: dict) -> str:
    """The bare table name for a node.

    Labels are not uniformly qualified — the graph carries `dbo.pipeline_brief_state`,
    `stortrack.dbo.all_comp_sf_dist`, `gold.fact_comp_rates` and bare `campaign`
    side by side — so the qualifier has to be removed rather than assumed.

    We strip the node's OWN database and schema off the front instead of taking
    the last dot-segment, because table names may contain dots themselves. Sites
    really does hold `AM List 08.08.2025 - Site List - 08.06.2025`, and splitting
    that on the last dot yields `2025`, which then matches nothing. Prefix
    removal gets every observed shape right, dotted names included.
    """
    label = str(node.get("label") or "").strip()
    for prefix in (node.get("database"), node.get("schema")):
        p = str(prefix or "").strip()
        if p and label.lower().startswith(p.lower() + "."):
            label = label[len(p) + 1:]
    return _norm(label)


# ── snapshot cache ────────────────────────────────────────────────────────────

_cache_lock = threading.Lock()
_cached: Optional[_Snapshot] = None
_cached_at: float = 0.0
# Set after a failed load so a server without the snapshot table doesn't pay for
# a failing cross-database query on every single question.
_failed_at: float = 0.0
_FAILURE_BACKOFF_SECONDS = 300


def load_snapshot(conn: ConnHandle) -> Optional[_Snapshot]:
    """Latest lineage snapshot, cached. Returns None when unavailable."""
    global _cached, _cached_at, _failed_at
    now = time.monotonic()
    with _cache_lock:
        if _cached is not None and now - _cached_at < _CACHE_TTL_SECONDS:
            return _cached
        if _cached is None and now - _failed_at < _FAILURE_BACKOFF_SECONDS:
            return None

    res = execute_query(
        conn,
        f"SELECT TOP 1 snapshot_date, captured_at, graph FROM {SNAPSHOT_TABLE} "
        f"ORDER BY captured_at DESC",
    )
    if res.get("error") or not res.get("rows"):
        logger.info("lineage snapshot unavailable: %s", res.get("error") or "no rows")
        with _cache_lock:
            _failed_at = time.monotonic()
        return None

    row = res["rows"][0]
    try:
        graph = json.loads(row[2])
    except (TypeError, ValueError) as e:
        logger.warning("lineage snapshot did not parse: %s", e)
        with _cache_lock:
            _failed_at = time.monotonic()
        return None

    snap = _Snapshot(graph, str(row[0] or ""), str(row[1] or ""))
    with _cache_lock:
        _cached, _cached_at, _failed_at = snap, time.monotonic(), 0.0
    logger.info(
        "lineage snapshot loaded: %s nodes / %s edges, captured %s",
        snap.node_count, snap.edge_count, snap.captured_at,
    )
    return snap


def reset_cache() -> None:
    """Drop the cached snapshot. For tests and for a forced refresh."""
    global _cached, _cached_at, _failed_at
    with _cache_lock:
        _cached, _cached_at, _failed_at = None, 0.0, 0.0


# ── live write stats ──────────────────────────────────────────────────────────

# Row counts and last-write time for every user table in one database.
#
# index_id in (0, 1) is the heap or clustered index — exactly one of the two
# exists per table, so this counts each table's rows once instead of once per
# nonclustered index. last_user_update is taken as the MAX across all of a
# table's indexes because a write touches whichever index it touches.
_WRITE_STATS_SQL = """
SELECT
    s.name AS table_schema,
    t.name AS table_name,
    SUM(CASE WHEN p.index_id IN (0, 1) THEN p.row_count ELSE 0 END) AS row_count,
    MAX(u.last_user_update) AS last_write
FROM sys.tables t
JOIN sys.schemas s ON s.schema_id = t.schema_id
LEFT JOIN sys.dm_db_partition_stats p ON p.object_id = t.object_id
LEFT JOIN sys.dm_db_index_usage_stats u
       ON u.object_id = t.object_id AND u.database_id = DB_ID()
GROUP BY s.name, t.name
"""


def fetch_write_stats(conn: ConnHandle) -> dict[tuple[str, str], tuple[Optional[int], Optional[str]]]:
    """Row count and last write time per (schema, table) for one SQL Server database.

    Returns an empty dict on any failure — the DMVs need VIEW DATABASE STATE,
    which not every connection has, and a missing freshness column is far better
    than a failed question.

    ``last_user_update`` is reset by a SQL Server restart, so a None here means
    "no write observed since the last restart", NOT "never written". Callers
    must not present it as proof a table is dead; it is corroborating evidence
    for a table lineage already shows has no writer.
    """
    if conn.dialect != "mssql":
        return {}
    res = execute_query(conn, _WRITE_STATS_SQL)
    if res.get("error"):
        logger.info("write stats unavailable: %s", res["error"])
        return {}

    out: dict[tuple[str, str], tuple[Optional[int], Optional[str]]] = {}
    for row in res.get("rows") or []:
        schema, table, rows, last = row[0], row[1], row[2], row[3]
        last_str = None
        if last is not None:
            last_str = str(last)[:19]  # trim sub-second noise
        out[(_norm(schema), _norm(table))] = (
            int(rows) if rows is not None else None,
            last_str,
        )
    return out


# ── rendering ─────────────────────────────────────────────────────────────────

def describe(facts: Optional[TableFacts]) -> str:
    """One-line summary of a table's standing, for the model's context.

    Written as terse clauses rather than prose: it is repeated once per
    candidate table, and the model reads it as structured evidence.
    """
    if facts is None:
        return "no lineage record"

    parts: list[str] = []

    if facts.writers:
        shown = facts.writers[:2]
        for w in shown:
            who = w["dag"] or w["endpoint"] or w["owner"] or "unnamed pipeline"
            script = f" ({w['script']})" if w["script"] and w["script"] != who else ""
            parts.append(f"written by {who}{script}")
        if len(facts.writers) > len(shown):
            parts.append(f"+{len(facts.writers) - len(shown)} more writers")
    elif facts.provenance in ("no_writer", "unreferenced"):
        parts.append(
            "NO WRITER — nothing in gitflow or wwg-reports maintains this"
            if facts.provenance == "no_writer"
            else "UNREFERENCED — no code reads or writes this"
        )
    elif facts.is_maintained:
        # Provenance says a pipeline builds it, but no write edge resolved to a
        # named DAG — usually a multi-step transform the extractor attributed to
        # an intermediate. Say what that means rather than leaking the enum.
        parts.append(
            "built by a pipeline (writer not resolved to a named DAG)"
            if facts.provenance == "derived"
            else "loaded from an external source"
        )

    if facts.is_missing:
        parts.append("MISSING FROM CATALOG — referenced in code but not a real table")
    if facts.is_retired:
        parts.append(f"lifecycle {facts.lifecycle}")
    if facts.has_broken_reads:
        parts.append("something still reads this and is failing")

    if facts.fan_out:
        parts.append(f"read by {facts.fan_out}")
        if facts.readers:
            parts.append(f"incl. {', '.join(facts.readers[:3])}")

    if facts.row_count is not None:
        parts.append(f"{facts.row_count:,} rows")
    if facts.last_write:
        parts.append(f"last write {facts.last_write}")
    elif facts.row_count is not None:
        parts.append("no write observed since the server last restarted")

    if facts.domain:
        parts.append(f"domain {facts.domain}")

    return "; ".join(parts) if parts else "no lineage record"


# How the model should read the annotations above. Appended to the find_data
# prompt so the flags are interpreted rather than parroted.
GUIDANCE = """\
Each candidate carries evidence from the data lineage graph, which is rebuilt \
daily by static analysis of the gitflow and wwg-reports repositories and \
reconciled against the live database catalogs. Use it — do not rank on name \
similarity alone.

- A table "written by <DAG>" is actively maintained. Prefer these. Name the DAG \
when you recommend one, so the user knows what to check if the data looks wrong.
- "NO WRITER" / "UNREFERENCED" means no pipeline in either repository populates \
it. It may still be loaded by hand or by something outside those repos, so do \
not claim it is empty or unused — but do not recommend it over a maintained \
table, and say plainly that nothing maintains it.
- "MISSING FROM CATALOG" means the table does not exist. Never recommend it.
- lifecycle backup / deprecated / dated_snapshot / test means it was retired. \
Mention it only to explain why you skipped it.
- "last write" is observed on the server. A maintained table whose last write is \
old is the interesting case — the pipeline exists but has stopped. Call that out \
specifically rather than treating it as healthy. Its absence means no write since \
the server last restarted, which is NOT evidence the table is dead.
- If the best name match is unmaintained and a maintained table is a slightly \
worse name match, recommend the maintained one and explain the trade.
"""
