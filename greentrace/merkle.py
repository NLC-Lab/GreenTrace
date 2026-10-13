"""Merkle root 与可验证 proof。"""
from .canonical import canonical_hash


def merkle_root(leaves: list[str]) -> str:
    if not leaves:
        return canonical_hash("")
    # Prefixes prevent a leaf/internal-node ambiguity (second-preimage attacks).
    level = [canonical_hash({"domain": "leaf", "value": leaf}) for leaf in leaves]
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [canonical_hash({"domain": "node", "left": level[i], "right": level[i + 1]})
                 for i in range(0, len(level), 2)]
    return level[0]


def merkle_proof(leaves: list[str], index: int) -> list[dict[str, str]]:
    if index < 0 or index >= len(leaves):
        raise IndexError(index)
    proof, level, idx = [], [canonical_hash({"domain": "leaf", "value": leaf}) for leaf in leaves], index
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        sibling = idx - 1 if idx % 2 else idx + 1
        proof.append({"position": "left" if idx % 2 else "right", "hash": level[sibling]})
        level = [canonical_hash({"domain": "node", "left": level[i], "right": level[i + 1]})
                 for i in range(0, len(level), 2)]
        idx //= 2
    return proof


def verify_proof(leaf: str, proof: list[dict[str, str]], root: str) -> bool:
    value = canonical_hash({"domain": "leaf", "value": leaf})
    for item in proof:
        value = canonical_hash({"domain": "node", "left": item["hash"], "right": value}
                               if item["position"] == "left" else
                               {"domain": "node", "left": value, "right": item["hash"]})
    return value == root
