"""Writing parse results to Postgres, one file per transaction.

Everything here uses SQLAlchemy Core (tables + statements), not the ORM: the
indexer moves thousands of rows in bulk, where an identity map and per-object
change tracking would only add overhead.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import bindparam, delete, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection

from core.db.models import CodeEdge, CodeNode, File
from core.parser.types import NodeRef, ParsedEdge, ParsedFile, ParsedNode

nodes_t = CodeNode.__table__
edges_t = CodeEdge.__table__
files_t = File.__table__

# Maps a parsed node's identity to its row id, for the whole repo. Kept up to
# date as files are written, so edges can point at nodes from other files.
IdMap = dict[NodeRef, int]


@dataclass(slots=True)
class FileWrite:
    """What writing one file changed."""

    nodes_inserted: int = 0
    nodes_updated: int = 0  # code changed: embedding/tsv cleared for recompute
    nodes_moved: int = 0  # same code, new line numbers (code above it changed)
    nodes_kept: int = 0
    nodes_deleted: int = 0
    edges_written: int = 0


@dataclass(frozen=True, slots=True)
class StoredFile:
    id: int
    content_hash: str


async def load_files(conn: AsyncConnection, repo_id: int) -> dict[str, StoredFile]:
    rows = await conn.execute(
        select(files_t.c.path, files_t.c.id, files_t.c.content_hash).where(
            files_t.c.repo_id == repo_id
        )
    )
    return {r.path: StoredFile(r.id, r.content_hash) for r in rows}


async def load_id_map(conn: AsyncConnection, repo_id: int) -> IdMap:
    rows = await conn.execute(
        select(nodes_t.c.id, nodes_t.c.qualified_name, nodes_t.c.ordinal).where(
            nodes_t.c.repo_id == repo_id
        )
    )
    return {NodeRef(r.qualified_name, r.ordinal): r.id for r in rows}


async def delete_files(
    conn: AsyncConnection, repo_id: int, paths: Sequence[str], id_map: IdMap
) -> int:
    """Remove files that no longer exist. Nodes cascade; edges *into* them from
    other files are kept with dst_node_id = NULL (FK ON DELETE SET NULL), ready
    to be re-linked if the target reappears. Returns the number of nodes removed."""
    if not paths:
        return 0
    file_ids = select(files_t.c.id).where(files_t.c.repo_id == repo_id, files_t.c.path.in_(paths))
    doomed = await conn.execute(
        select(nodes_t.c.qualified_name, nodes_t.c.ordinal).where(nodes_t.c.file_id.in_(file_ids))
    )
    removed = 0
    for row in doomed:
        id_map.pop(NodeRef(row.qualified_name, row.ordinal), None)
        removed += 1
    await conn.execute(
        delete(files_t).where(files_t.c.repo_id == repo_id, files_t.c.path.in_(paths))
    )
    return removed


async def write_file(
    conn: AsyncConnection,
    repo_id: int,
    parsed: ParsedFile,
    edges: Iterable[ParsedEdge],
    stored: StoredFile | None,
    id_map: IdMap,
) -> FileWrite:
    """Bring one file's rows in line with its parse: file row, node-level diff,
    then all edges leaving this file's nodes."""
    result = FileWrite()
    file_id = await _upsert_file(conn, repo_id, parsed, stored)

    existing = {
        NodeRef(r.qualified_name, r.ordinal): r
        for r in await conn.execute(
            select(
                nodes_t.c.id,
                nodes_t.c.qualified_name,
                nodes_t.c.ordinal,
                nodes_t.c.kind,
                nodes_t.c.content_hash,
                nodes_t.c.start_line,
                nodes_t.c.end_line,
            ).where(nodes_t.c.file_id == file_id)
        )
    }
    parsed_refs = {n.ref for n in parsed.nodes}

    # 1. Nodes that disappeared from this file.
    gone = [ref for ref in existing if ref not in parsed_refs]
    if gone:
        await conn.execute(delete(nodes_t).where(nodes_t.c.id.in_([existing[r].id for r in gone])))
        for ref in gone:
            id_map.pop(ref, None)
        result.nodes_deleted = len(gone)

    # 2. Classify the rest: unchanged, moved, changed, new.
    changed: list[dict[str, object]] = []
    moved: list[dict[str, object]] = []
    new: list[dict[str, object]] = []
    for node in parsed.nodes:
        row = existing.get(node.ref)
        if row is None:
            new.append(_node_values(repo_id, file_id, node))
        elif row.content_hash != node.content_hash or row.kind != node.kind:
            changed.append({"b_id": row.id, **_node_values(repo_id, file_id, node)})
        elif (row.start_line, row.end_line) != (node.start_line, node.end_line):
            moved.append({"b_id": row.id, "start_line": node.start_line, "end_line": node.end_line})
        else:
            result.nodes_kept += 1

    if changed:
        # Content changed: the embedding and tsv describe the old code, so clear
        # them; the embedding job recomputes exactly the NULL ones (Phase 2).
        await conn.execute(
            update(nodes_t)
            .where(nodes_t.c.id == bindparam("b_id"))
            .values(embedding=None, tsv=None),
            changed,
        )
        result.nodes_updated = len(changed)
    if moved:
        await conn.execute(update(nodes_t).where(nodes_t.c.id == bindparam("b_id")), moved)
        result.nodes_moved = len(moved)
    if new:
        inserted = await conn.execute(
            insert(nodes_t).returning(nodes_t.c.id, nodes_t.c.qualified_name, nodes_t.c.ordinal),
            new,
        )
        for row in inserted:
            id_map[NodeRef(row.qualified_name, row.ordinal)] = row.id
        result.nodes_inserted = len(new)

    # 3. Edges: replace every edge leaving this file's nodes. Targets not in the
    # DB yet (a file later in this run) are stored unresolved and filled in by
    # relink_edges() at the end of the run.
    source_ids = [id_map[ref] for ref in parsed_refs]
    await conn.execute(delete(edges_t).where(edges_t.c.src_node_id.in_(source_ids)))
    edge_rows = [
        {
            "repo_id": repo_id,
            "src_node_id": id_map[edge.source],
            "dst_node_id": id_map.get(edge.target) if edge.target else None,
            "kind": edge.kind,
            "target_name": edge.target_name,
        }
        for edge in edges
    ]
    if edge_rows:
        await conn.execute(insert(edges_t), edge_rows)
    result.edges_written = len(edge_rows)
    return result


async def relink_edges(conn: AsyncConnection, repo_id: int) -> int:
    """Point unresolved edges at nodes whose qualified name now exists.

    Covers edges written before their target file was inserted, and edges whose
    target was deleted and has since reappeared. With duplicate names, the
    lowest ordinal wins (the same rule the resolver uses). When nothing
    matches, it writes nothing.

    Written as a per-edge index lookup on purpose. A join form (UPDATE ... FROM
    a DISTINCT ON subquery) took 0.2 s or 300 s on the same data, depending on
    whether table statistics were fresh after a bulk load: with stale stats the
    planner picked a nested loop over every node per edge. This form is driven
    by the partial index on unresolved edges and probes
    ix_code_nodes_repo_id_qualified_name once per edge, so its cost doesn't
    depend on statistics.
    """
    result = await conn.execute(
        text(
            """
            UPDATE code_edges AS e
            SET dst_node_id = (
                SELECT n.id FROM code_nodes AS n
                WHERE n.repo_id = e.repo_id AND n.qualified_name = e.target_name
                ORDER BY n.ordinal, n.id
                LIMIT 1
            )
            WHERE e.repo_id = :repo_id
              AND e.dst_node_id IS NULL
              AND EXISTS (
                  SELECT 1 FROM code_nodes AS n
                  WHERE n.repo_id = e.repo_id AND n.qualified_name = e.target_name
              )
            """
        ),
        {"repo_id": repo_id},
    )
    return result.rowcount


async def analyze_graph_tables(conn: AsyncConnection) -> None:
    """Refresh planner statistics after a bulk write. Autovacuum would get to
    it eventually, but queries right after a large index run would otherwise be
    planned as if the tables were nearly empty."""
    await conn.execute(text("ANALYZE files, code_nodes, code_edges"))


async def _upsert_file(
    conn: AsyncConnection, repo_id: int, parsed: ParsedFile, stored: StoredFile | None
) -> int:
    values = {
        "language": parsed.language.value,
        "content_hash": parsed.content_hash,
        "is_test": parsed.is_test,
        "size_bytes": parsed.size_bytes,
    }
    if stored is not None:
        await conn.execute(update(files_t).where(files_t.c.id == stored.id).values(**values))
        return stored.id
    row = await conn.execute(
        insert(files_t).values(repo_id=repo_id, path=parsed.path, **values).returning(files_t.c.id)
    )
    return row.scalar_one()


def _node_values(repo_id: int, file_id: int, node: ParsedNode) -> dict[str, object]:
    return {
        "repo_id": repo_id,
        "file_id": file_id,
        "kind": node.kind,
        "name": node.name,
        "qualified_name": node.qualified_name,
        "ordinal": node.ordinal,
        "signature": node.signature,
        "start_line": node.start_line,
        "end_line": node.end_line,
        "code": node.code,
        "docstring": node.docstring,
        "content_hash": node.content_hash,
    }


async def count_graph(conn: AsyncConnection, repo_id: int) -> Mapping[str, int]:
    """Row counts actually in the DB (the summary prints these, not parse counts)."""
    nodes = await conn.scalar(select(func.count()).where(nodes_t.c.repo_id == repo_id))
    edges = await conn.scalar(select(func.count()).where(edges_t.c.repo_id == repo_id))
    resolved = await conn.scalar(
        select(func.count()).where(edges_t.c.repo_id == repo_id, edges_t.c.dst_node_id.is_not(None))
    )
    return {"nodes": nodes or 0, "edges": edges or 0, "edges_resolved": resolved or 0}
