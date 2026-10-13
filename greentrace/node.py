from __future__ import annotations
import time
import json
import copy
import os
import uuid
import threading
from typing import Any, Literal, cast
from decimal import Decimal
from pathlib import Path
from .crypto import Identity
from .models import AuditEvent, Block, Transaction, TransactionResult, Organization, TraceEvent
from .contract import GreenContract
from .consensus import PBFT
from .storage import ChainStorage
from .merkle import merkle_root, merkle_proof


class Node:
    _locks: dict[str, threading.RLock] = {}
    _locks_guard = threading.Lock()
    def __init__(self, name: str, validators: list[str], data_dir: str | Path | None = None):
        self.name = name
        self.chain_id = os.getenv("GREENTRACE_CHAIN_ID", "greentrace-sim")
        self._lock_key = str(Path(data_dir or ".").resolve() / name)
        with self._locks_guard:
            self._lock = self._locks.setdefault(self._lock_key, threading.RLock())
        key_path = Path(data_dir) / f"{name}.identity" if data_dir else None
        if key_path and key_path.exists():
            self.identity = Identity.from_private_key(key_path.read_text(encoding="utf-8").strip())
        else:
            self.identity = Identity()
            if key_path:
                key_path.parent.mkdir(parents=True, exist_ok=True)
                key_path.write_text(self.identity.export_private_key(), encoding="utf-8")
        self.consensus = PBFT(validators, data_dir)
        self.storage = ChainStorage(Path(data_dir) / f"{name}.sqlite3") if data_dir else None
        self.chain = self.storage.load() if self.storage else []
        self.contract = GreenContract()
        self.seen_requests: set[str] = set()
        self.nonces: dict[str, int] = {}
        self.transaction_status: dict[str, dict] = {}
        self.audit_events: list[AuditEvent] = []
        self.trace_events: list[TraceEvent] = []
        self._state_path = Path(data_dir) / f"{name}.state.json" if data_dir else None
        self._load_state()
        # A deterministic node identity is the bootstrap authority for a new
        # local network.  It is persisted with the identity file and prevents
        # a fresh database from becoming permanently ungovernable.
        if not self.contract.roles:
            self.contract.grant(self.address, "admin")
            self._save_state()

    def _load_state(self) -> None:
        if self._state_path and self._state_path.exists():
            state = json.loads(self._state_path.read_text(encoding="utf-8"))
            self.contract.roles = state.get("roles", {})
            self.contract.products = state.get("products", {})
            self.contract.balances = state.get("balances", {})
            self.contract.frozen_accounts = set(state.get("frozen_accounts", []))
            self.contract.ledger = state.get("ledger", [])
            self.contract.orders = state.get("orders", {})
            self.contract.supply_chain = state.get("supply_chain", [])
            self.contract._rewards = set(state.get("rewards", []))
            self.contract.organizations = {
                k: Organization.model_validate(v) for k, v in state.get("organizations", {}).items()
            }
            for product in self.contract.products.values():
                product["versions"] = set(product.get("versions", []))
                for field in ("carbon_kg", "remaining_credits", "retired_credits"):
                    if field in product:
                        product[field] = Decimal(str(product[field]))
                product["holdings"] = {k: Decimal(str(v)) for k, v in
                                       product.get("holdings", {product.get("owner", ""): product.get("remaining_credits", 0)}).items()}
            self.seen_requests = set(state.get("seen_requests", []))
            self.nonces = {k: int(v) for k, v in state.get("nonces", {}).items()}
            self.transaction_status = state.get("transaction_status", {})
            self.audit_events = [AuditEvent.model_validate(x) for x in state.get("audit_events", [])]
            self.trace_events = [TraceEvent.model_validate(x) for x in state.get("trace_events", [])]

    def _save_state(self) -> None:
        if self._state_path:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            products = {
                key: {**value, "versions": sorted(value.get("versions", set()))}
                for key, value in self.contract.products.items()
            }
            payload = {
                "roles": self.contract.roles, "organizations": {
                    k: v.model_dump() for k, v in self.contract.organizations.items()
                }, "products": products, "balances": self.contract.balances,
                "frozen_accounts": sorted(self.contract.frozen_accounts),
                "ledger": self.contract.ledger, "orders": self.contract.orders,
                "supply_chain": self.contract.supply_chain,
                "rewards": sorted(self.contract._rewards),
                "seen_requests": sorted(self.seen_requests),
                "nonces": self.nonces, "transaction_status": self.transaction_status,
                "audit_events": [x.model_dump() for x in self.audit_events],
                "trace_events": [x.model_dump() for x in self.trace_events],
            }
            # UUID makes concurrent writers safe even when a thread is reused.
            temporary = self._state_path.with_name(
                self._state_path.name + f".{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp")
            payload = self._json_safe(payload)
            with temporary.open("w", encoding="utf-8") as stream:
                stream.write(json.dumps(payload, ensure_ascii=False, indent=2))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._state_path)

    @property
    def address(self) -> str:
        return self.identity.address

    def submit(self, tx: Transaction) -> None:
        self._validate_transaction(tx)
        self.contract.apply(tx.sender, tx.action, tx.payload)
        self.seen_requests.add(tx.request_id)
        self.nonces[tx.sender] = tx.nonce

    def _validate_transaction(self, tx: Transaction) -> None:
        if tx.chain_id != self.chain_id:
            raise ValueError("wrong chain_id")
        if tx.request_id in self.seen_requests:
            raise ValueError("duplicate request_id")
        if tx.nonce != self.nonces.get(tx.sender, 0) + 1:
            raise ValueError("invalid or replayed nonce")
        if not Identity.from_public_key(tx.public_key).verify(tx.signing_bytes(), tx.signature):
            raise ValueError("invalid Ed25519 signature")
        if Identity.from_public_key(tx.public_key).address != tx.sender:
            raise ValueError("sender does not match public key")

    @staticmethod
    def _json_safe(value):
        if isinstance(value, set):
            return sorted(value)
        if isinstance(value, dict):
            return {str(k): Node._json_safe(v) for k, v in value.items()}
        if isinstance(value, list):
            return [Node._json_safe(v) for v in value]
        if isinstance(value, Decimal):
            return str(value)
        return value

    def _state_summary(self, tx: Transaction) -> dict:
        """Keep trace payloads small while retaining the affected business objects."""
        batch_id = tx.payload.get("batch_id") or tx.payload.get("product_id")
        order_id = tx.payload.get("order_id")
        addresses = [tx.sender]
        for key in ("to", "buyer", "supplier", "manufacturer", "address", "account"):
            if tx.payload.get(key):
                addresses.append(str(tx.payload[key]))
        result: dict[str, Any] = {"balances": {a: self.contract.balances.get(a, {}) for a in dict.fromkeys(addresses)}}
        if batch_id:
            result["batch"] = self._json_safe(self.contract.products.get(str(batch_id), {}))
        if order_id:
            result["order"] = self._json_safe(self.contract.orders.get(str(order_id), {}))
        result["ledger_entries"] = len(self.contract.ledger)
        return self._json_safe(result)

    def _trace(self, tx: Transaction, stage: str, height: int | None,
               reason: str | None = None, before: dict | None = None,
               after: dict | None = None, timestamp: float | None = None) -> TraceEvent:
        trace_stage = cast(Literal["received", "validated", "authorized", "contract_applied",
                                    "consensus", "committed", "rejected"], stage)
        entity_type = entity_id = None
        if tx.payload.get("order_id"):
            entity_type, entity_id = "order", str(tx.payload["order_id"])
        elif tx.payload.get("batch_id") or tx.payload.get("product_id"):
            entity_type, entity_id = "batch", str(tx.payload.get("batch_id") or tx.payload.get("product_id"))
        event = TraceEvent(
            event_id=uuid.uuid4().hex,
            stage=trace_stage, actor=tx.sender, request_id=tx.request_id, tx_hash=tx.tx_hash(),
            block_height=height, timestamp=timestamp if timestamp is not None else time.time(),
            reason=reason, before=before or {}, after=after or {},
            entity_type=entity_type, entity_id=entity_id)
        self.trace_events.append(event)
        return event

    def trace_for_request(self, request_id: str) -> list[TraceEvent]:
        return [x for x in self.trace_events if x.request_id == request_id]

    get_trace = trace_for_request

    def order_timeline(self, order_id: str) -> list[TraceEvent]:
        return [x for x in self.trace_events if x.entity_type == "order" and x.entity_id == order_id]

    get_order_timeline = order_timeline

    def batch_timeline(self, batch_id: str) -> list[TraceEvent]:
        return [x for x in self.trace_events if x.entity_type == "batch" and x.entity_id == batch_id]

    get_batch_timeline = batch_timeline

    def funds_flow(self, address: str) -> list[dict]:
        return [entry for entry in self.contract.ledger
                if entry.get("from") == address or entry.get("to") == address
                or entry.get("buyer") == address or entry.get("supplier") == address]

    get_funds_flow = funds_flow

    def register_organization(self, organization_id: str, name: str) -> Organization:
        organization = self.contract.register_organization(organization_id, name)
        self._record_event("organization.created", self.address, details=organization.model_dump())
        self._save_state()
        return organization

    def add_member(self, organization_id: str, address: str, role: str,
                   expires_at: float | None = None):
        member = self.contract.add_member(organization_id, address, role, expires_at)
        self._record_event("member.credential_issued", address, details=member.model_dump())
        self._save_state()
        return member

    def invite_member(self, organization_id: str, address: str, role: str,
                      expires_at: float | None = None):
        member = self.contract.invite_member(organization_id, address, role, expires_at)
        self._record_event("member.invited", self.address, details=member.model_dump())
        self._save_state()
        return member

    def member_decision(self, address: str, accept: bool):
        member = self.contract.member_decision(address, accept)
        self._record_event("member.invitation_" + ("accepted" if accept else "rejected"),
                           address, details=member.model_dump())
        self._save_state()
        return member

    def deactivate_organization(self, organization_id: str) -> None:
        self.contract.deactivate_organization(organization_id)
        self._record_event("organization.deactivated", self.address,
                           details={"organization_id": organization_id})
        self._save_state()

    def deactivate_member(self, address: str) -> None:
        self.contract.deactivate_member(address)
        self._record_event("member.credential_revoked", self.address, details={"address": address})
        self._save_state()

    def _record_event(self, event_type: str, actor: str, request_id: str | None = None,
                      details: dict | None = None) -> AuditEvent:
        event = AuditEvent(event_id=uuid.uuid4().hex, event_type=event_type, actor=actor,
                           request_id=request_id, timestamp=time.time(), details=details or {})
        self.audit_events.append(event)
        return event

    def append_block(self, transactions: list[Transaction], votes: dict[str, str] | None = None) -> Block:
        with self._lock:
            return self._append_block_locked(transactions, votes)

    def _append_block_locked(self, transactions: list[Transaction], votes: dict[str, str] | None = None) -> Block:
        previous = self.chain[-1].hash if self.chain else "0" * 64
        block = Block.create(len(self.chain), previous, self.address, time.time(), transactions, self.chain_id)
        for tx in transactions:
            self._trace(tx, "received", block.height, timestamp=block.timestamp)
        votes = votes or {node: self.consensus.vote_digest(block.hash, "commit", node)
                          for node in self.consensus.validators}
        if not self.consensus.certify(block.hash, votes):
            for tx in transactions:
                self._reject(tx, "PBFT quorum not reached", block.height, block.timestamp)
            self._save_state()
            raise ValueError("PBFT quorum not reached")
        # Validate and apply only after quorum, keeping rejected proposals atomic.
        snapshot = (copy.deepcopy(self.contract.products), dict(self.contract.roles),
                    copy.deepcopy(self.contract.organizations), set(self.seen_requests),
                    dict(self.nonces), dict(self.transaction_status), list(self.audit_events),
                    copy.deepcopy(self.contract.balances), set(self.contract.frozen_accounts),
                    copy.deepcopy(self.contract.ledger), copy.deepcopy(self.contract.orders),
                    copy.deepcopy(self.contract.supply_chain), set(self.contract._rewards),
                    list(self.trace_events))
        try:
            for tx in transactions:
                self._validate_transaction(tx)
                self._trace(tx, "validated", block.height, timestamp=block.timestamp)
                self.contract.authorize_member(tx.sender, tx.action)
                self._trace(tx, "authorized", block.height, timestamp=block.timestamp)
                before = self._state_summary(tx)
                self.submit(tx)
                after = self._state_summary(tx)
                self._trace(tx, "contract_applied", block.height, before=before, after=after,
                            timestamp=block.timestamp)
                self._trace(tx, "consensus", block.height, timestamp=block.timestamp)
                self.transaction_status[tx.request_id] = TransactionResult(
                    accepted=True, tx_hash=tx.tx_hash(), height=block.height
                ).model_dump()
                self._trace(tx, "committed", block.height, after=after, timestamp=block.timestamp)
                self._record_event("transaction.committed", tx.sender, tx.request_id,
                                   {"action": tx.action, "tx_hash": tx.tx_hash()})
        except Exception as exc:
            (self.contract.products, self.contract.roles, self.contract.organizations,
             self.seen_requests, self.nonces, self.transaction_status,
             self.audit_events, self.contract.balances, self.contract.frozen_accounts,
             self.contract.ledger, self.contract.orders, self.contract.supply_chain,
             self.contract._rewards, self.trace_events) = snapshot
            # The attempted state transition is removed; the rejection remains queryable.
            for tx in transactions:
                self._reject(tx, str(exc), block.height, block.timestamp)
            self._save_state()
            raise
        block.signatures = votes
        self.chain.append(block)
        try:
            if self.storage:
                self.storage.append(block)
            self._save_state()
        except Exception:
            self.chain.pop()
            (self.contract.products, self.contract.roles, self.contract.organizations,
             self.seen_requests, self.nonces, self.transaction_status,
             self.audit_events, self.contract.balances, self.contract.frozen_accounts,
             self.contract.ledger, self.contract.orders, self.contract.supply_chain,
             self.contract._rewards, self.trace_events) = snapshot
            raise
        return block

    def _reject(self, tx: Transaction, reason: str, height: int | None,
                timestamp: float | None = None) -> None:
        before = self._state_summary(tx)
        self._trace(tx, "rejected", height, reason=reason, before=before,
                    after=before, timestamp=timestamp)
        existing = self.transaction_status.get(tx.request_id)
        if not existing or existing.get("status") != "committed":
            self.transaction_status[tx.request_id] = TransactionResult(
                accepted=False, tx_hash=tx.tx_hash(), status="rejected").model_dump()

    def transaction_result(self, request_id: str) -> TransactionResult | None:
        result = self.transaction_status.get(request_id)
        return TransactionResult.model_validate(result) if result else None

    def rebuild_state(self) -> bool:
        """Validate blocks and deterministically rebuild governance and business state.

        Governance state (organizations, members, roles) is derived from the chain only.
        Replay therefore starts from an empty registry seeded with the deterministic
        bootstrap administrator:

        * keeping the in-memory organizations would make replaying
          ``governance_register_organization`` fail with "organization already exists"
          (i.e. ``POST /integrity/rebuild`` returned 409 on any live node that had ever
          used a governance transaction);
        * keeping the roles loaded from the local state file would let a role injected
          into that file survive a rebuild.
        """
        with self._lock:
            if not self.verify_full():
                raise ValueError("chain integrity check failed")
            nonces: dict[str, int] = {}
            seen: set[str] = set()
            status: dict[str, dict[str, Any]] = {}
            original_products = self.contract.products
            original_organizations = self.contract.organizations
            original_roles = dict(self.contract.roles)
            original_dynamic = (self.contract.balances, self.contract.frozen_accounts,
                                self.contract.ledger, self.contract.orders,
                                self.contract.supply_chain, self.contract._rewards)
            self.contract.products = {}
            self.contract.organizations = {}
            self.contract.balances = {}
            self.contract.frozen_accounts = set()
            self.contract.ledger = []
            self.contract.orders = {}
            self.contract.supply_chain = []
            self.contract._rewards = set()
            # Genesis authority: the node's persisted identity, and nothing else.
            self.contract.roles = {self.address: "admin"}
            try:
                for block in self.chain:
                    for tx in block.transactions:
                        if tx.request_id in seen or tx.nonce != nonces.get(tx.sender, 0) + 1:
                            raise ValueError("invalid transaction sequence")
                        # Replay with the block timestamp so that credential expiry and
                        # order deadlines are judged as of the block, not as of "now".
                        self.contract.apply(tx.sender, tx.action, tx.payload, now=block.timestamp)
                        seen.add(tx.request_id)
                        nonces[tx.sender] = tx.nonce
                        status[tx.request_id] = TransactionResult(
                            accepted=True, tx_hash=tx.tx_hash(), height=block.height
                        ).model_dump()
            except Exception:
                self.contract.products = original_products
                self.contract.organizations = original_organizations
                self.contract.roles = original_roles
                (self.contract.balances, self.contract.frozen_accounts, self.contract.ledger,
                 self.contract.orders, self.contract.supply_chain,
                 self.contract._rewards) = original_dynamic
                raise
            self.seen_requests, self.nonces, self.transaction_status = seen, nonces, status
            self._save_state()
            return True

    def integrity_report(self) -> dict[str, object]:
        valid = self.verify_full()
        consistent = self.verify_state()
        return {"valid": valid, "state_consistent": consistent,
                "height": len(self.chain),
                "tip": self.chain[-1].hash if self.chain else "0" * 64}

    @staticmethod
    def _state_digest(contract, seen_requests, nonces):
        """规范化核心业务状态，用于把「链重放结果」与 live 状态做一致性比较。

        只包含能从链确定性重建的字段；rejected trace / 审计事件是链外追加记录，
        不参与一致性比较（它们本来就不从链重建）。
        """
        return {
            "roles": dict(contract.roles),
            "organizations": {k: v.model_dump() for k, v in contract.organizations.items()},
            "products": Node._json_safe(contract.products),
            "balances": Node._json_safe(contract.balances),
            "frozen_accounts": sorted(contract.frozen_accounts),
            "ledger": Node._json_safe(contract.ledger),
            "orders": Node._json_safe(contract.orders),
            "supply_chain": Node._json_safe(contract.supply_chain),
            "rewards": sorted(contract._rewards),
            "seen_requests": sorted(seen_requests),
            "nonces": {k: int(v) for k, v in nonces.items()},
        }

    def _live_digest(self):
        return self._state_digest(self.contract, self.seen_requests, self.nonces)

    def _replay_digest(self):
        contract = GreenContract()
        contract.roles = {self.address: "admin"}
        nonces: dict[str, int] = {}
        seen: set[str] = set()
        for block in self.chain:
            for tx in block.transactions:
                if tx.request_id in seen or tx.nonce != nonces.get(tx.sender, 0) + 1:
                    raise ValueError("invalid transaction sequence")
                contract.apply(tx.sender, tx.action, tx.payload, now=block.timestamp)
                seen.add(tx.request_id)
                nonces[tx.sender] = tx.nonce
        return self._state_digest(contract, seen, nonces)

    def verify_state(self) -> bool:
        """检测 live 状态是否与从链确定性重放得到的状态一致。

        若 state.json 被手工篡改（改余额、账本、订单、角色、组织等），返回 False。
        """
        try:
            return self._live_digest() == self._replay_digest()
        except (ValueError, TypeError, KeyError):
            return False

    def verify_chain(self) -> bool:
        previous = "0" * 64
        for height, block in enumerate(self.chain):
            if block.height != height or block.previous_hash != previous or block.chain_id != self.chain_id:
                return False
            if block.merkle_root != merkle_root(
                [tx.tx_hash() for tx in block.transactions]
            ) or block.calculate_hash() != block.hash:
                return False
            previous = block.hash
        return True

    def verify_full(self) -> bool:
        """Verify chain links, transaction signatures, Merkle roots, and vote tickets."""
        if not self.verify_chain():
            return False
        # Validate historical sequence independently of mutable live state.  This
        # catches nonce/request-id reuse while still allowing verification after a
        # state snapshot has been deleted or rebuilt.
        historical_nonces: dict[str, int] = {}
        historical_requests: set[str] = set()
        for block in self.chain:
            for tx in block.transactions:
                try:
                    if tx.chain_id != self.chain_id or tx.request_id in historical_requests:
                        return False
                    if tx.nonce != historical_nonces.get(tx.sender, 0) + 1:
                        return False
                    identity = Identity.from_public_key(tx.public_key)
                    if identity.address != tx.sender or not identity.verify(
                            tx.signing_bytes(), tx.signature):
                        return False
                    historical_requests.add(tx.request_id)
                    historical_nonces[tx.sender] = tx.nonce
                except (ValueError, TypeError, KeyError):
                    return False
            if not self.consensus.certify(block.hash, block.signatures):
                return False
        return True

    def query(self, collection: str, offset: int = 0, limit: int = 100):
        if offset < 0 or limit < 1 or limit > 1000:
            raise ValueError("invalid pagination")
        values = cast(list[Any] | None, {"chain": self.chain, "ledger": self.contract.ledger,
                  "audit_events": self.audit_events, "trace_events": self.trace_events}.get(collection))
        if values is None:
            raise ValueError("unknown collection")
        return values[offset:offset + limit]

    def merkle_proof(self, height: int, tx_index: int) -> tuple[str, list[dict[str, str]], str]:
        block = self.chain[height]
        leaves = [tx.tx_hash() for tx in block.transactions]
        return leaves[tx_index], merkle_proof(leaves, tx_index), block.merkle_root
