import time
import pytest

from greentrace.canonical import canonical_hash
from greentrace.crypto import Identity
from greentrace.models import Transaction
from greentrace.node import Node


def tx(node, identity, request, nonce, action, payload):
    return node.append_block([Transaction.signed(identity, request, nonce, action, payload)])


def test_invitation_lifecycle_and_organization_deactivation():
    node = Node("membership-edge", ["n1", "n2", "n3", "n4"])
    member = Identity()
    node.register_organization("org", "Org")
    invited = node.invite_member("org", member.address, "enterprise",
                                 expires_at=time.time() + 60)
    assert invited.invitation_status == "invited"
    with pytest.raises(ValueError, match="credential inactive|permission denied"):
        tx(node, member, "blocked", 1, "register_batch", {"batch_id": "x"})
    assert node.member_decision(member.address, True).active
    node.deactivate_organization("org")
    with pytest.raises(ValueError, match="organization inactive"):
        tx(node, member, "inactive", 1, "register_batch", {"batch_id": "x"})


def test_carbon_evidence_and_batch_freeze_lifecycle():
    node = Node("carbon-edge", ["n1", "n2", "n3", "n4"])
    owner, auditor, regulator = Identity(), Identity(), Identity()
    node.contract.grant(owner.address, "enterprise")
    node.contract.grant(auditor.address, "auditor")
    node.contract.grant(regulator.address, "regulator")
    tx(node, owner, "b", 1, "register_batch", {"batch_id": "b", "carbon_kg": 2})
    with pytest.raises(ValueError, match="evidence_hash"):
        tx(node, owner, "missing", 2, "submit_carbon", {"batch_id": "b", "version": "v1"})
    evidence = {"meter": 42}
    with pytest.raises(ValueError, match="mismatch"):
        tx(node, owner, "bad", 2, "submit_carbon",
           {"batch_id": "b", "version": "v1", "evidence": evidence, "evidence_hash": "bad"})
    tx(node, owner, "good", 2, "submit_carbon",
       {"batch_id": "b", "version": "v1", "evidence": evidence,
        "evidence_hash": canonical_hash(evidence)})
    tx(node, auditor, "audit", 1, "audit_carbon", {"batch_id": "b", "status": "approved"})
    tx(node, regulator, "freeze", 1, "freeze_credits", {"batch_id": "b"})
    with pytest.raises(ValueError, match="frozen"):
        tx(node, owner, "retire", 3, "retire_credits", {"batch_id": "b", "amount": 1})
    tx(node, regulator, "unfreeze", 2, "unfreeze_credits", {"batch_id": "b"})


def test_order_confirmation_timeout_and_supplier_rejection_refund():
    node = Node("order-edge", ["n1", "n2", "n3", "n4"])
    issuer, buyer, supplier = Identity(), Identity(), Identity()
    node.contract.grant(issuer.address, "issuer")
    node.contract.grant(buyer.address, "buyer")
    node.contract.grant(supplier.address, "supplier")
    tx(node, issuer, "mint", 1, "mint_tokens",
       {"to": buyer.address, "amount": 10, "token": "CarbonCoin"})
    tx(node, buyer, "create", 1, "create_order",
       {"order_id": "o", "supplier": supplier.address, "manufacturer": supplier.address,
        "amount": 10, "confirmation_deadline": 10})
    with pytest.raises(ValueError, match="timed out"):
        tx(node, supplier, "late", 1, "acknowledge_order", {"order_id": "o", "now": 11})
    tx(node, supplier, "reject", 1, "supplier_reject_order",
       {"order_id": "o", "reason": "capacity"})
    assert node.contract.orders["o"]["status"] == "supplier_rejected"
    assert node.contract.balance(buyer.address) == 10
