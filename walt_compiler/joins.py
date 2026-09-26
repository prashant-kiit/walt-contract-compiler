"""Join planning over declared relationships (DESIGN.md §6.1).

Breadth-first search from the base (fact) dataset, walking relationships only in their declared
direction and only when they cannot fan out base rows (many_to_one, one_to_one). Neighbours are
visited in declaration order, so the plan is deterministic. A dataset reachable by two distinct
shortest paths is ambiguous: the compiler refuses rather than picks one.
"""
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from walt_compiler.errors import AmbiguousJoinPath, NoJoinPath, UnsupportedRelationship
from walt_compiler.model import Catalog, Relationship

FOLLOWABLE = frozenset({"many_to_one", "one_to_one"})


@dataclass(frozen=True)
class JoinStep:
    from_dataset: str
    from_column: str
    to_dataset: str
    to_column: str


@dataclass
class _Search:
    order: list[str]                     # datasets in discovery order (base excluded)
    parent: dict[str, Relationship]      # the relationship a dataset was first reached by
    paths: dict[str, int]                # number of shortest paths, capped at 2


def _search(catalog: Catalog, base: str, follow: Callable[[Relationship], bool]) -> _Search:
    depth, found = {base: 0}, _Search([], {}, {base: 1})
    queue = deque([base])
    while queue:
        current = queue.popleft()
        for rel in catalog.relationships:
            if rel.from_dataset != current or not follow(rel):
                continue
            nxt = rel.to_dataset
            if nxt not in depth:
                depth[nxt] = depth[current] + 1
                found.order.append(nxt)
                found.parent[nxt] = rel
                found.paths[nxt] = found.paths[current]
                queue.append(nxt)
            elif depth[nxt] == depth[current] + 1:
                found.paths[nxt] = min(2, found.paths[nxt] + found.paths[current])
    return found


def _describe(rel: Relationship) -> str:
    return (f"{rel.from_dataset}.{rel.from_column} -> {rel.to_dataset}.{rel.to_column} "
            f"({rel.cardinality})")


def plan_joins(catalog: Catalog, base: str, needed: Mapping[str, str]) -> tuple[JoinStep, ...]:
    """Joins that attach every needed dataset to `base`.

    `needed` maps each dataset to the contract path that required it, so errors point at the
    request that caused them.
    """
    safe = _search(catalog, base, lambda rel: rel.cardinality in FOLLOWABLE)
    included: set[str] = set()
    for dataset, path in needed.items():
        if dataset == base:
            continue
        if dataset not in safe.parent:
            anyway = _search(catalog, base, lambda rel: True)
            if dataset not in anyway.parent:
                raise NoJoinPath(f"no relationship path from {base!r} to {dataset!r}.", path=path)
            node = dataset
            while anyway.parent[node].cardinality in FOLLOWABLE:
                node = anyway.parent[node].from_dataset
            raise UnsupportedRelationship(
                f"reaching {dataset!r} from {base!r} needs {_describe(anyway.parent[node])}, "
                f"which would fan out rows of {base!r} and inflate aggregates.", path=path)
        if safe.paths[dataset] > 1:
            raise AmbiguousJoinPath(
                f"more than one shortest relationship path from {base!r} to {dataset!r}; "
                f"the model must make the path unique.", path=path)
        node = dataset
        while node != base:
            included.add(node)
            node = safe.parent[node].from_dataset
    return tuple(JoinStep(rel.from_dataset, rel.from_column, rel.to_dataset, rel.to_column)
                 for rel in (safe.parent[d] for d in safe.order if d in included))
