"""ChromaDB-based codebase index for providing full context to LLM reviews.

Indexes Python source files into chunks (functions/classes) so the LLM can see
related code — not just the diff — when reviewing changes.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

INDEX_DIR = Path(__file__).parent / ".poke_index"

SOURCE_DIRS = ["app", "api", "lib", "jobs", "migrations/versions"]
FUNC_CLASS_RE = re.compile(
    r"^(class\s+\w+|def\s+\w+|async\s+def\s+\w+)", re.MULTILINE
)


def _split_into_chunks(content: str, file_path: str) -> list[dict]:
    """Split a Python file into function/class-level chunks."""
    lines = content.split("\n")
    chunks: list[dict] = []
    current_chunk_start = 0
    current_header = f"# top-level: {file_path}"

    for i, line in enumerate(lines):
        if FUNC_CLASS_RE.match(line):
            if i > current_chunk_start:
                chunk_text = "\n".join(lines[current_chunk_start:i]).strip()
                if chunk_text and len(chunk_text) > 20:
                    chunks.append({
                        "id": f"{file_path}:{current_chunk_start}",
                        "text": chunk_text[:3000],
                        "metadata": {"file": file_path, "header": current_header, "line": current_chunk_start},
                    })
            current_chunk_start = i
            current_header = line.strip()

    # Last chunk
    if current_chunk_start < len(lines):
        chunk_text = "\n".join(lines[current_chunk_start:]).strip()
        if chunk_text and len(chunk_text) > 20:
            chunks.append({
                "id": f"{file_path}:{current_chunk_start}",
                "text": chunk_text[:3000],
                "metadata": {"file": file_path, "header": current_header, "line": current_chunk_start},
            })

    return chunks


def _collect_source_files(repo_root: str) -> list[Path]:
    """Collect all .py files from source directories."""
    files: list[Path] = []
    root = Path(repo_root)
    for src_dir in SOURCE_DIRS:
        target = root / src_dir
        if target.is_dir():
            files.extend(target.rglob("*.py"))
    return sorted(files)


def _compute_hash(files: list[Path]) -> str:
    """Hash file paths + mtimes to detect staleness."""
    h = hashlib.md5()
    for f in files:
        h.update(f"{f}:{f.stat().st_mtime_ns}".encode())
    return h.hexdigest()


def build_index(repo_root: str | None = None) -> "chromadb.Collection":
    """Build or load the ChromaDB index of the codebase."""
    import chromadb

    if repo_root is None:
        repo_root = os.getcwd()

    INDEX_DIR.mkdir(parents=True, exist_ok=True)

    client = chromadb.PersistentClient(path=str(INDEX_DIR))
    source_files = _collect_source_files(repo_root)
    current_hash = _compute_hash(source_files)

    collection_name = "poke_codebase"

    # Check if we need to rebuild
    hash_file = INDEX_DIR / "index_hash"
    needs_rebuild = True
    if hash_file.exists():
        stored_hash = hash_file.read_text().strip()
        if stored_hash == current_hash:
            try:
                collection = client.get_collection(collection_name)
                if collection.count() > 0:
                    needs_rebuild = False
                    logger.info("Codebase index is fresh (%d chunks)", collection.count())
            except Exception:
                pass

    if needs_rebuild:
        logger.info("Building codebase index from %d source files…", len(source_files))

        try:
            client.delete_collection(collection_name)
        except Exception:
            pass

        collection = client.create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

        all_chunks: list[dict] = []
        for file_path in source_files:
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                rel_path = str(file_path.relative_to(repo_root))
                chunks = _split_into_chunks(content, rel_path)
                all_chunks.extend(chunks)
            except Exception:
                continue

        if all_chunks:
            batch_size = 100
            for i in range(0, len(all_chunks), batch_size):
                batch = all_chunks[i:i + batch_size]
                collection.add(
                    ids=[c["id"] for c in batch],
                    documents=[c["text"] for c in batch],
                    metadatas=[c["metadata"] for c in batch],
                )

        hash_file.write_text(current_hash)
        logger.info("Indexed %d chunks into ChromaDB", len(all_chunks))

    return client.get_collection(collection_name)


def query_context(collection, diff_text: str, file_path: str, n_results: int = 5) -> list[str]:
    """Query the index for code chunks related to a diff.

    Returns a list of relevant code snippets from across the codebase that
    help the LLM understand the full picture (e.g., where a removed parameter moved to).
    """
    query_parts = []

    # Extract function/class names mentioned in the diff
    identifiers = set(re.findall(r"\b(?:def|class)\s+(\w+)", diff_text))
    called_funcs = set(re.findall(r"(\w+)\s*\(", diff_text))
    relevant_ids = identifiers | (called_funcs - {"if", "for", "while", "return", "print", "len", "str", "int", "list", "dict", "set", "type", "isinstance", "any", "all"})

    if relevant_ids:
        query_parts.append(" ".join(sorted(relevant_ids)))
    else:
        query_parts.append(diff_text[:500])

    try:
        results = collection.query(
            query_texts=query_parts,
            n_results=n_results,
            where={"file": {"$ne": file_path}},
        )
    except Exception:
        try:
            results = collection.query(
                query_texts=query_parts,
                n_results=n_results,
            )
        except Exception:
            return []

    context_chunks: list[str] = []
    if results and results.get("documents"):
        for doc_list in results["documents"]:
            for doc in doc_list:
                if doc and len(doc.strip()) > 20:
                    context_chunks.append(doc)

    return context_chunks
