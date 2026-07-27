"""Overlap injection for chunked documents.

Post-chunking pass: injects +/-1k char overlap from neighboring
chunks of the same file, demarcated with structural markers.

T0 scope: _inject_overlap only. 01_Chunk.py imports this.
"""

from collections import defaultdict

_OVERLAP_CHARS = 1000


def _inject_overlap(nodes: list) -> list:
    """Add +/-1k char overlap from neighboring chunks, per file.

    Groups nodes by file_path, sorts by chunk_index within each
    group, then prepends/appends neighbor text with demarcation
    markers.
    """
    by_file = defaultdict(list)
    for node in nodes:
        fpath = node.metadata.get("file_path", "")
        by_file[fpath].append(node)

    for fpath, file_nodes in by_file.items():
        file_nodes.sort(key=lambda n: n.metadata.get("chunk_index", 0))

        # Snapshot original texts before mutation
        original_texts = [n.text for n in file_nodes]

        for i, node in enumerate(file_nodes):
            # Front overlap: up to 1k chars from preceding chunk(s)
            front = ""
            j = i - 1
            while j >= 0 and len(front) < _OVERLAP_CHARS:
                take = original_texts[j][-(_OVERLAP_CHARS - len(front)):]
                front = take + front
                j -= 1

            # Back overlap: up to 1k chars from following chunk(s)
            back = ""
            j = i + 1
            while j < len(file_nodes) and len(back) < _OVERLAP_CHARS:
                take = original_texts[j][:_OVERLAP_CHARS - len(back)]
                back += take
                j += 1

            # Demarcate and inject
            parts = []
            if front:
                parts.append(f"--- context ---\n{front}\n--- end context ---\n")
            parts.append(node.text)
            if back:
                parts.append(f"\n--- context ---\n{back}\n--- end context ---")

            node.text = "\n".join(parts)

    return nodes
