import pytest

from greentrace.crypto import Identity
from greentrace.models import Transaction
from greentrace.node import Node


def setup_node():
    node = Node("test", ["n1", "n2", "n3", "n4"])
    producer, auditor, regulator = Identity(), Identity(), Identity()
    node.contract.grant(producer.address, "enterprise")
    node.contract.grant(auditor.address, "auditor")
    node.contract.grant(regulator.address, "regulator")
    return node, producer, auditor, regulator


def test_forged_signature_and_unauthorized_are_rejected():
    node, producer, _, _ = setup_node()
    tx = Transaction.signed(producer, "forge", 1, "register_batch", {"batch_id": "B1", "carbon_kg": 10})
    tx.signature = Identity().sign(tx.signing_bytes())
    with pytest.raises(ValueError, match="signature"):
        node.append_block([tx])


def test_pbft_requires_quorum_and_detects_tamper():
    node, producer, _, _ = setup_node()
    tx = Transaction.signed(producer, "b1", 1, "register_batch", {"batch_id": "B1", "carbon_kg": 10})
    with pytest.raises(ValueError, match="quorum"):
        node.append_block([tx], {"n1": "wrong", "n2": "wrong"})
    # The failed proposal did not mutate the chain or business state.
    assert node.chain == []
    assert node.contract.products == {}
    node.append_block([tx])
    assert node.verify_chain()
    node.chain[0].transactions[0].payload["carbon_kg"] = 999
    assert not node.verify_chain()


def test_merkle_proof_idempotency_and_credit_rules():
    node, producer, auditor, _ = setup_node()
    node.append_block([Transaction.signed(producer, "b1", 1, "register_batch",
                                          {"batch_id": "B1", "carbon_kg": 10})])
    node.append_block([Transaction.signed(producer, "c1", 2, "submit_carbon",
                                          {"batch_id": "B1", "version": "v1", "evidence_hash": "abc"})])
    with pytest.raises(ValueError, match="duplicate"):
        node.append_block([Transaction.signed(producer, "c1", 2, "submit_carbon",
                                              {"batch_id": "B1", "version": "v1"})])
    with pytest.raises(ValueError, match="permission denied|own"):
        node.append_block([Transaction.signed(producer, "a1", 3, "audit_carbon",
                                              {"batch_id": "B1", "passed": True})])
    node.append_block([Transaction.signed(auditor, "a2", 1, "audit_carbon",
                                          {"batch_id": "B1", "passed": True})])
    node.append_block([Transaction.signed(producer, "t1", 3, "transfer_credits",
                                          {"batch_id": "B1", "to": "buyer", "amount": 10})])
    with pytest.raises(ValueError, match="exceeds"):
        node.append_block([Transaction.signed(producer, "t2", 4, "transfer_credits",
                                              {"batch_id": "B1", "to": "buyer", "amount": 1})])
    leaf, proof, root = node.merkle_proof(0, 0)
    from greentrace.merkle import verify_proof
    assert verify_proof(leaf, proof, root)


def test_organization_membership_credential_expiry_and_audit():
    node, producer, _, _ = setup_node()
    node.register_organization("org-1", "Demo Enterprise")
    member = node.add_member("org-1", producer.address, "enterprise", expires_at=0)
    assert member.credential_version == 1
    tx = Transaction.signed(producer, "expired", 1, "register_batch",
                            {"batch_id": "B-expired", "carbon_kg": 1})
    with pytest.raises(ValueError, match="inactive or expired"):
        node.append_block([tx])
    assert any(event.event_type == "member.credential_issued" for event in node.audit_events)


def test_transaction_status_and_rebuild_state():
    node = Node("rebuild", ["n1", "n2", "n3", "n4"])
    producer = Identity()
    # Roles must be reproducible from the chain, otherwise replaying the block below
    # fails with "permission denied": grant them with governance transactions.
    node.append_block([Transaction.signed(
        node.identity, "gov-org", 1, "governance_register_organization",
        {"organization_id": "org-rebuild", "name": "Rebuild Org"})])
    node.append_block([Transaction.signed(
        node.identity, "gov-member", 2, "governance_add_member",
        {"organization_id": "org-rebuild", "address": producer.address, "role": "enterprise"})])
    tx = Transaction.signed(producer, "rebuild-1", 1, "register_batch",
                            {"batch_id": "B-rebuild", "carbon_kg": 2})
    block = node.append_block([tx])
    assert node.transaction_result("rebuild-1").status == "committed"
    node.contract.products.clear()
    assert node.rebuild_state()
    assert node.contract.products["B-rebuild"]["carbon_kg"] == 2
    assert node.contract.roles[producer.address] == "enterprise"
    assert node.integrity_report()["tip"] == block.hash


def test_retirement_is_irreversible_and_idempotent_receipt():
    node, producer, auditor, _ = setup_node()
    node.append_block([Transaction.signed(producer, "rb", 1, "register_batch",
                                          {"batch_id": "B-retire", "carbon_kg": 3})])
    node.append_block([Transaction.signed(auditor, "ra", 1, "audit_carbon",
                                          {"batch_id": "B-retire", "passed": True})])
    tx = Transaction.signed(producer, "rr", 2, "retire_credits",
                            {"batch_id": "B-retire", "amount": 2, "reason": "offset"})
    first = node.append_block([tx])
    assert node.contract.products["B-retire"]["remaining_credits"] == 1
    assert node.contract.products["B-retire"]["retired_credits"] == 2
    assert node.transaction_result("rr").tx_hash == tx.tx_hash()
    assert first.transactions[0].request_id == "rr"
    with pytest.raises(ValueError, match="duplicate"):
        node.append_block([tx])


def test_token_freeze_and_supply_chain_settlement():
    node = Node("ecosystem", ["n1", "n2", "n3", "n4"])
    issuer, buyer, supplier, manufacturer = (Identity() for _ in range(4))
    for identity, role in ((issuer, "issuer"), (buyer, "buyer"),
                           (supplier, "supplier"), (manufacturer, "manufacturer")):
        node.contract.grant(identity.address, role)

    def submit(identity, request_id, nonce, action, payload):
        return node.append_block([Transaction.signed(
            identity, request_id, nonce, action, payload
        )])

    submit(issuer, "mint-buyer", 1, "mint_tokens",
           {"token": "CarbonCoin", "to": buyer.address, "amount": 100})
    submit(issuer, "mint-supplier", 2, "mint_tokens",
           {"token": "CarbonCoin", "to": supplier.address, "amount": 1})
    submit(buyer, "order-1", 1, "create_order", {
        "order_id": "O-1", "supplier": supplier.address,
        "manufacturer": manufacturer.address, "buyer": buyer.address,
        "amount": 60, "token": "CarbonCoin",
    })
    submit(supplier, "ack-1", 1, "acknowledge_order", {"order_id": "O-1"})
    submit(supplier, "deliver-1", 2, "deliver_order",
           {"order_id": "O-1", "on_time": True})
    submit(buyer, "accept-1", 2, "accept_order", {"order_id": "O-1"})
    assert node.contract.balance(buyer.address) == 40
    assert node.contract.balance(supplier.address) == 61
    assert node.contract.orders["O-1"]["status"] == "settled"
    assert any(x["type"] == "order_settlement" for x in node.contract.ledger)
    with pytest.raises(ValueError, match="invalid order state|already settled"):
        submit(buyer, "settle-1", 3, "settle_order", {"order_id": "O-1"})

    node.contract.frozen_accounts.add(buyer.address)
    with pytest.raises(ValueError, match="frozen"):
        submit(buyer, "frozen-transfer", 3, "transfer_tokens",
               {"token": "CarbonCoin", "to": supplier.address, "amount": 1})
