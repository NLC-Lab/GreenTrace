from __future__ import annotations
from pathlib import Path
import os
import logging
import hmac
from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel, Field
from .models import Transaction, TransactionResult, TraceEvent
from .merkle import verify_proof
from .node import Node


app = FastAPI(title="GreenTrace 联盟链仿真平台", version="0.2.0")
logging.basicConfig(level=getattr(logging, os.getenv("GREENTRACE_LOG_LEVEL", "INFO").upper(), logging.INFO))
_data = Path(os.getenv("GREENTRACE_DATA_DIR", str(Path(__file__).resolve().parents[1] / "data")))
_node = Node("node-1", ["node-1", "node-2", "node-3", "node-4"], _data)


def api_security(x_api_key: str | None = Header(default=None)) -> None:
    """Optional deployment hook: set GREENTRACE_API_KEY to protect writes."""
    expected = os.getenv("GREENTRACE_API_KEY")
    if expected and (x_api_key is None or
                     not hmac.compare_digest(x_api_key.encode(), expected.encode())):
        raise HTTPException(status_code=401, detail="missing or invalid API key")


class MerkleRequest(BaseModel):
    leaf: str
    proof: list[dict[str, str]] = Field(default_factory=list)
    root: str


class OrganizationRequest(BaseModel):
    organization_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=200)


class MemberRequest(BaseModel):
    organization_id: str
    address: str
    role: str
    expires_at: float | None = None


class CredentialRevocationRequest(BaseModel):
    address: str


class OrganizationActionRequest(BaseModel):
    organization_id: str


class MemberDecisionRequest(BaseModel):
    address: str
    accept: bool


@app.get("/health")
def health():
    return {"status": "ok", "height": len(_node.chain)}


@app.get("/chain")
def chain(offset: int = 0, limit: int = 100):
    return [b.model_dump() for b in _node.query("chain", offset, limit)]

@app.get("/nodes")
def nodes():
    return {"validators": _node.consensus.validators, "fault_tolerance": _node.consensus.f,
            "quorum": _node.consensus.quorum, "local_address": _node.address}

@app.get("/batches")
def batches(offset: int = 0, limit: int = 100):
    if offset < 0 or limit < 1 or limit > 1000:
        raise HTTPException(status_code=400, detail="invalid pagination")
    values = list(_node.contract.products.values())
    return values[offset:offset + limit]

@app.get("/accounts/{address}")
def account(address: str):
    return {"address": address, "frozen": address in _node.contract.frozen_accounts,
            "balances": _node.contract.balances.get(address, {})}

@app.get("/ledger")
def ledger(offset: int = 0, limit: int = 100):
    return _node.query("ledger", offset, limit)

@app.get("/orders")
def orders(offset: int = 0, limit: int = 100):
    if offset < 0 or limit < 1 or limit > 1000:
        raise HTTPException(status_code=400, detail="invalid pagination")
    values = list(_node.contract.orders.values())
    return values[offset:offset + limit]

@app.get("/orders/{order_id}")
def order(order_id: str):
    if order_id not in _node.contract.orders:
        raise HTTPException(status_code=404, detail="order not found")
    return _node.contract.orders[order_id]

@app.get("/supply-chain")
def supply_chain():
    return _node.contract.supply_chain

@app.get("/audit-chain")
def audit_chain():
    return {"valid": _node.verify_chain(), "height": len(_node.chain),
            "blocks": [b.model_dump() for b in _node.chain]}

@app.get("/integrity")
def integrity():
    return _node.integrity_report()

@app.get("/audit-events")
def audit_events(offset: int = 0, limit: int = 100):
    if offset < 0 or limit < 1 or limit > 1000:
        raise HTTPException(status_code=400, detail="invalid pagination")
    values = [event.model_dump() for event in _node.audit_events]
    return values[offset:offset + limit]


@app.get("/traces/{request_id}", response_model=list[TraceEvent])
@app.get("/transactions/{request_id}/trace", response_model=list[TraceEvent])
@app.get("/trace/{request_id}", response_model=list[TraceEvent])
def transaction_trace(request_id: str):
    events = _node.trace_for_request(request_id)
    if not events:
        raise HTTPException(status_code=404, detail="trace not found")
    return events


@app.get("/orders/{order_id}/timeline", response_model=list[TraceEvent])
@app.get("/orders/{order_id}/trace", response_model=list[TraceEvent])
def order_timeline(order_id: str):
    events = _node.order_timeline(order_id)
    if not events:
        raise HTTPException(status_code=404, detail="order timeline not found")
    return events


@app.get("/batches/{batch_id}/timeline", response_model=list[TraceEvent])
@app.get("/batches/{batch_id}/trace", response_model=list[TraceEvent])
def batch_timeline(batch_id: str):
    events = _node.batch_timeline(batch_id)
    if not events:
        raise HTTPException(status_code=404, detail="batch timeline not found")
    return events


@app.get("/accounts/{address}/funds")
@app.get("/accounts/{address}/funds-flow")
def funds_flow(address: str):
    return {"address": address, "balance": _node.contract.balances.get(address, {}),
            "entries": _node.funds_flow(address)}

@app.get("/transactions/{request_id}", response_model=TransactionResult)
def transaction_status(request_id: str):
    result = _node.transaction_result(request_id)
    if result is None:
        raise HTTPException(status_code=404, detail="transaction not found")
    return result

@app.get("/merkle-proof/{height}/{tx_index}")
def merkle_proof_endpoint(height: int, tx_index: int):
    try:
        leaf, proof, root = _node.merkle_proof(height, tx_index)
    except (IndexError, KeyError) as exc:
        raise HTTPException(status_code=404, detail="transaction not found") from exc
    return {"leaf": leaf, "proof": proof, "root": root}

@app.post("/merkle-proof/verify")
def verify_merkle(body: MerkleRequest):
    return {"valid": verify_proof(body.leaf, body.proof, body.root)}


@app.post("/transactions", response_model=TransactionResult, dependencies=[Depends(api_security)])
def transaction(tx: Transaction):
    existing = _node.transaction_result(tx.request_id)
    if existing is not None:
        if existing.tx_hash != tx.tx_hash():
            raise HTTPException(status_code=409, detail="request_id already used by another transaction")
        return existing
    try:
        _node.append_block([tx])
        return TransactionResult(accepted=True, tx_hash=tx.tx_hash(), height=len(_node.chain) - 1)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@app.post("/governance/transactions", response_model=TransactionResult,
          dependencies=[Depends(api_security)])
def governance_transaction(tx: Transaction):
    if not tx.action.startswith("governance_"):
        raise HTTPException(status_code=422, detail="action must start with governance_")
    return transaction(tx)

def _action_endpoint(expected: str, tx: Transaction):
    if tx.action != expected:
        raise HTTPException(status_code=422, detail=f"action must be {expected}")
    return transaction(tx)

@app.post("/carbon-data", dependencies=[Depends(api_security)])
def carbon_data(tx: Transaction):
    return _action_endpoint("submit_carbon", tx)

@app.post("/audit-confirmations", dependencies=[Depends(api_security)])
def audit_confirmation(tx: Transaction):
    return _action_endpoint("audit_carbon", tx)

@app.post("/transfers", dependencies=[Depends(api_security)])
def transfer(tx: Transaction):
    return _action_endpoint("transfer_credits", tx)

@app.post("/tokens/mint", dependencies=[Depends(api_security)])
def mint_token(tx: Transaction):
    return _action_endpoint("mint_tokens", tx)

@app.post("/tokens/transfer", dependencies=[Depends(api_security)])
def transfer_token(tx: Transaction):
    return _action_endpoint("transfer_tokens", tx)

@app.post("/accounts/freeze", dependencies=[Depends(api_security)])
def freeze_account(tx: Transaction):
    return _action_endpoint("freeze_account", tx)

@app.post("/accounts/unfreeze", dependencies=[Depends(api_security)])
def unfreeze_account(tx: Transaction):
    return _action_endpoint("unfreeze_account", tx)

@app.post("/supply-chain/orders", dependencies=[Depends(api_security)])
def create_order(tx: Transaction):
    return _action_endpoint("create_order", tx)

@app.post("/supply-chain/orders/{order_id}/actions", dependencies=[Depends(api_security)])
def order_action(order_id: str, tx: Transaction):
    if tx.payload.get("order_id") != order_id:
        raise HTTPException(status_code=422, detail="order_id mismatch")
    if tx.action not in {"acknowledge_order", "deliver_order", "partial_delivery",
                         "accept_order", "partial_accept", "settle_order", "cancel_order",
                         "reject_order", "dispute_order", "resolve_dispute",
                         "refund_order", "release_escrow", "supplier_reject_order",
                         "extend_order", "quality_reject", "adjudicate_order"}:
        raise HTTPException(status_code=422, detail="unsupported order action")
    return transaction(tx)

@app.post("/retirements", dependencies=[Depends(api_security)])
def retirement(tx: Transaction):
    return _action_endpoint("retire_credits", tx)

@app.post("/organizations")
def create_organization(body: OrganizationRequest, _: None = Depends(api_security)):
    raise HTTPException(status_code=403, detail="governance changes require a signed governance transaction")

@app.post("/organizations/members")
def add_member(body: MemberRequest, _: None = Depends(api_security)):
    raise HTTPException(status_code=403, detail="governance changes require a signed governance transaction")


@app.post("/organizations/members/invite")
def invite_member(body: MemberRequest, _: None = Depends(api_security)):
    raise HTTPException(status_code=403, detail="governance changes require a signed governance transaction")


@app.post("/organizations/members/decision")
def member_decision(body: MemberDecisionRequest, _: None = Depends(api_security)):
    raise HTTPException(status_code=403, detail="governance changes require a signed governance transaction")

@app.post("/organizations/members/revoke")
def revoke_member(body: CredentialRevocationRequest, _: None = Depends(api_security)):
    raise HTTPException(status_code=403, detail="governance changes require a signed governance transaction")


@app.post("/organizations/deactivate")
def deactivate_organization(body: OrganizationActionRequest, _: None = Depends(api_security)):
    raise HTTPException(status_code=403, detail="governance changes require a signed governance transaction")

@app.post("/integrity/rebuild", dependencies=[Depends(api_security)])
def rebuild_state():
    try:
        return {"rebuilt": _node.rebuild_state(), **_node.integrity_report()}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def run():
    import uvicorn
    uvicorn.run("greentrace.api:app", host="127.0.0.1", port=8000, reload=False)
