"""简化 PBFT：提案、2f+1 prepare/commit，适用于本地仿真。"""
from pathlib import Path
from .canonical import canonical_hash
from .crypto import Identity


class PBFT:
    def __init__(self, validators: list[str], key_dir: str | Path | None = None):
        self.validators = validators
        self.f = (len(validators) - 1) // 3
        self._proposals: set[tuple[int, str]] = set()
        self._identities: dict[str, Identity] = {}
        for validator in validators:
            key_path = Path(key_dir) / f"validator-{validator}.key" if key_dir else None
            if key_path and key_path.exists():
                identity = Identity.from_private_key(key_path.read_text(encoding="utf-8").strip())
            else:
                identity = Identity()
                if key_path:
                    key_path.parent.mkdir(parents=True, exist_ok=True)
                    key_path.write_text(identity.export_private_key(), encoding="utf-8")
            self._identities[validator] = identity

    @property
    def quorum(self) -> int:
        return 2 * self.f + 1

    def certify(self, block_hash: str, votes: dict[str, str]) -> bool:
        valid = set()
        for node, vote in votes.items():
            if node not in self.validators:
                continue
            identity = self._identities[node]
            if identity.verify(self.vote_bytes(block_hash, "commit", node), vote):
                valid.add(node)
        return len(valid) >= self.quorum

    def propose(self, height: int, block_hash: str) -> None:
        key = (height, block_hash)
        if any(existing_height == height for existing_height, _ in self._proposals):
            raise ValueError("stale or duplicate proposal")
        self._proposals.add(key)

    def commit(self, height: int, block_hash: str, votes: dict[str, str]) -> bool:
        if (height, block_hash) not in self._proposals:
            raise ValueError("proposal not found")
        return self.certify(block_hash, votes)

    def vote_digest(self, block_hash: str, phase: str, node: str) -> str:
        if node not in self._identities:
            raise ValueError("unknown validator")
        return self._identities[node].sign(self.vote_bytes(block_hash, phase, node))

    @staticmethod
    def vote_bytes(block_hash: str, phase: str, node: str) -> bytes:
        return canonical_hash({"block": block_hash, "phase": phase, "node": node}).encode("ascii")
