from greentrace.crypto import Identity
from greentrace.merkle import merkle_proof, merkle_root, verify_proof
from greentrace.models import Transaction
from greentrace.node import Node


def test_signature_merkle_and_pbft():
    identity = Identity()
    tx = Transaction.signed(identity, "r1", 1, "noop", {})
    assert identity.verify(tx.signing_bytes(), tx.signature)
    leaves = [tx.tx_hash(), "other"]
    assert verify_proof(leaves[0], merkle_proof(leaves, 0), merkle_root(leaves))


def test_idempotency_nonce_and_contract():
    producer, auditor = Identity(), Identity()
    node = Node("node-1", ["node-1", "node-2", "node-3", "node-4"])
    node.contract.grant(producer.address, "producer")
    node.contract.grant(auditor.address, "auditor")
    tx = Transaction.signed(producer, "same", 1, "create_product", {"product_id": "p"})
    node.append_block([tx])
    try:
        node.append_block([tx])
        assert False
    except ValueError as exc:
        assert "duplicate" in str(exc)
    audit = Transaction.signed(auditor, "a", 1, "audit_product", {"product_id": "p", "passed": True})
    node.append_block([audit])
    assert node.contract.products["p"]["audited"]
