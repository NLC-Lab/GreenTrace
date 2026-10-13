from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, Field, ConfigDict
from .canonical import canonical_hash, canonical_json
from .crypto import Identity
from .merkle import merkle_root


class Transaction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str
    sender: str
    nonce: int = Field(ge=1)
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)
    signature: str = ""
    public_key: str = ""
    chain_id: str = "greentrace-sim"

    def signing_bytes(self) -> bytes:
        return canonical_json(self.model_dump(exclude={"signature", "public_key"}))

    def tx_hash(self) -> str:
        return canonical_hash(self.model_dump())

    @classmethod
    def signed(cls, identity: Identity, request_id: str, nonce: int, action: str,
               payload: dict[str, Any], chain_id: str = "greentrace-sim") -> "Transaction":
        tx = cls(request_id=request_id, sender=identity.address, nonce=nonce, action=action,
                 payload=payload, public_key=identity.export_public_key(), chain_id=chain_id)
        tx.signature = identity.sign(tx.signing_bytes())
        return tx


class TransactionResult(BaseModel):
    accepted: bool
    tx_hash: str
    height: int | None = None
    status: Literal["committed", "rejected", "pending"] = "committed"


class AuditEvent(BaseModel):
    event_id: str
    event_type: str
    actor: str
    request_id: str | None = None
    timestamp: float
    details: dict[str, Any] = Field(default_factory=dict)


class TraceEvent(BaseModel):
    """Immutable, query-friendly business trace entry for one transaction stage."""
    event_id: str
    stage: Literal["received", "validated", "authorized", "contract_applied",
                   "consensus", "committed", "rejected"]
    actor: str
    request_id: str
    tx_hash: str
    block_height: int | None = None
    timestamp: float
    reason: str | None = None
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)
    entity_type: str | None = None
    entity_id: str | None = None


class Member(BaseModel):
    address: str
    organization_id: str
    role: str
    active: bool = True
    credential_version: int = 1
    expires_at: float | None = None
    invitation_status: Literal["invited", "accepted", "rejected", "revoked"] = "accepted"


class Organization(BaseModel):
    organization_id: str
    name: str
    active: bool = True
    members: dict[str, Member] = Field(default_factory=dict)


class Block(BaseModel):
    height: int
    previous_hash: str
    proposer: str
    timestamp: float
    transactions: list[Transaction]
    merkle_root: str
    hash: str = ""
    signatures: dict[str, str] = Field(default_factory=dict)
    chain_id: str = "greentrace-sim"

    def header(self) -> dict[str, Any]:
        return self.model_dump(exclude={"hash", "signatures", "transactions"})

    def calculate_hash(self) -> str:
        return canonical_hash({**self.header(), "transactions": [t.model_dump() for t in self.transactions]})

    @classmethod
    def create(cls, height: int, previous_hash: str, proposer: str, timestamp: float, transactions: list[Transaction],
               chain_id: str = "greentrace-sim") -> "Block":
        root = merkle_root([t.tx_hash() for t in transactions])
        block = cls(height=height, previous_hash=previous_hash, proposer=proposer, timestamp=timestamp,
                    transactions=transactions, merkle_root=root, chain_id=chain_id)
        block.hash = block.calculate_hash()
        return block
