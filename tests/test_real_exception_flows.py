import pytest

from greentrace.crypto import Identity
from greentrace.models import Transaction
from greentrace.node import Node


def test_carbon_rework_and_new_version():
    node = Node("exceptions", ["n1", "n2", "n3", "n4"])
    enterprise, auditor = Identity(), Identity()
    node.contract.grant(enterprise.address, "enterprise")
    node.contract.grant(auditor.address, "auditor")

    def put(who, rid, nonce, action, payload):
        return node.append_block([Transaction.signed(who, rid, nonce, action, payload)])

    put(enterprise, "b", 1, "register_batch", {"batch_id": "B", "carbon_kg": 5})
    put(enterprise, "v1", 2, "submit_carbon",
        {"batch_id": "B", "version": "v1", "evidence_hash": "h1"})
    put(auditor, "back", 1, "audit_carbon",
        {"batch_id": "B", "status": "rework_required", "reason": "missing meter"})
    put(enterprise, "v2", 3, "submit_carbon",
        {"batch_id": "B", "version": "v2", "evidence_hash": "h2"})
    put(auditor, "ok", 2, "audit_carbon",
        {"batch_id": "B", "status": "approved"})
    assert node.contract.products["B"]["carbon_status"] == "approved"
    assert len(node.batch_timeline("B")) >= 5


def test_partial_delivery_dispute_refund_is_escrowed_and_idempotent():
    node = Node("orders-exceptions", ["n1", "n2", "n3", "n4"])
    issuer, buyer, supplier, regulator = (Identity() for _ in range(4))
    for identity, role in ((issuer, "issuer"), (buyer, "buyer"),
                           (supplier, "supplier"), (regulator, "regulator")):
        node.contract.grant(identity.address, role)

    def put(who, rid, nonce, action, payload):
        return node.append_block([Transaction.signed(who, rid, nonce, action, payload)])

    put(issuer, "mint", 1, "mint_tokens",
        {"to": buyer.address, "amount": 100, "token": "CarbonCoin"})
    put(buyer, "create", 1, "create_order",
        {"order_id": "O", "supplier": supplier.address, "manufacturer": supplier.address,
         "amount": 60, "token": "CarbonCoin"})
    assert node.contract.balance("escrow:O") == 60
    put(supplier, "ack", 1, "acknowledge_order", {"order_id": "O"})
    put(supplier, "deliver", 2, "partial_delivery",
        {"order_id": "O", "amount": 30, "reason": "capacity"})
    put(buyer, "accept", 2, "partial_accept", {"order_id": "O", "amount": 20})
    put(buyer, "dispute", 3, "dispute_order",
        {"order_id": "O", "reason": "short shipment", "evidence_hash": "eh"})
    put(regulator, "resolve", 1, "resolve_dispute",
        {"order_id": "O", "decision": "refund", "reason": "verified"})
    assert node.contract.orders["O"]["status"] == "resolved"
    assert node.contract.balance(buyer.address) == 80
    with pytest.raises(ValueError, match="invalid order state"):
        put(regulator, "resolve-again", 2, "resolve_dispute",
            {"order_id": "O", "decision": "refund", "reason": "duplicate"})
    with pytest.raises(ValueError, match="permission denied"):
        put(buyer, "bad", 4, "resolve_dispute",
            {"order_id": "O", "decision": "accept", "reason": "no"})
