from greentrace.crypto import Identity
from greentrace.models import Transaction
from greentrace.node import Node


def test_success_trace_and_rejection_are_queryable():
    node = Node("trace", ["n1", "n2", "n3", "n4"])
    issuer, buyer = Identity(), Identity()
    node.contract.grant(issuer.address, "issuer")
    node.contract.grant(buyer.address, "buyer")
    mint = Transaction.signed(issuer, "trace-mint", 1, "mint_tokens",
                              {"to": buyer.address, "amount": 10})
    node.append_block([mint])
    assert [e.stage for e in node.trace_for_request("trace-mint")] == [
        "received", "validated", "authorized", "contract_applied",
        "consensus", "committed"
    ]
    rejected = Transaction.signed(buyer, "trace-fail", 1, "transfer_tokens",
                                  {"to": issuer.address, "amount": 99})
    try:
        node.append_block([rejected])
    except ValueError:
        pass
    assert node.transaction_result("trace-fail").status == "rejected"
    assert node.trace_for_request("trace-fail")[-1].stage == "rejected"
    assert node.chain[-1].transactions[0].request_id == "trace-mint"


def test_order_timeline_funds_and_rebuild_keep_trace(tmp_path):
    node = Node("trace", ["n1", "n2", "n3", "n4"], tmp_path)
    issuer, buyer, supplier, manufacturer = (Identity() for _ in range(4))
    # This test rebuilds the node, so the grants have to be chain-derived.
    node.append_block([Transaction.signed(
        node.identity, "gov-org", 1, "governance_register_organization",
        {"organization_id": "org-trace", "name": "Trace Org"})])
    for index, (identity, role) in enumerate(
            ((issuer, "issuer"), (buyer, "buyer"),
             (supplier, "supplier"), (manufacturer, "manufacturer")), start=2):
        node.append_block([Transaction.signed(
            node.identity, f"gov-member-{index}", index, "governance_add_member",
            {"organization_id": "org-trace", "address": identity.address, "role": role})])

    def submit(identity, request, nonce, action, payload):
        node.append_block([Transaction.signed(identity, request, nonce, action, payload)])

    submit(issuer, "fund", 1, "mint_tokens",
           {"to": buyer.address, "amount": 20, "token": "CarbonCoin"})
    submit(buyer, "order", 1, "create_order",
           {"order_id": "O-TRACE", "supplier": supplier.address,
            "manufacturer": manufacturer.address, "amount": 10})
    submit(supplier, "ack", 1, "acknowledge_order", {"order_id": "O-TRACE"})
    submit(supplier, "deliver", 2, "deliver_order", {"order_id": "O-TRACE"})
    submit(buyer, "accept", 2, "accept_order", {"order_id": "O-TRACE"})
    before = [event.model_dump() for event in node.trace_events]
    assert node.order_timeline("O-TRACE")[-1].after["order"]["status"] == "settled"
    assert any(entry["type"] == "order_settlement"
               for entry in node.funds_flow(buyer.address))
    node.contract.orders.clear()
    node.rebuild_state()
    assert [event.model_dump() for event in node.trace_events] == before
