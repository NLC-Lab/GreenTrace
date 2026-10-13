from .crypto import Identity
from .models import Transaction
from .node import Node
from pprint import pprint


def main() -> None:
    identities = {role: Identity() for role in
                  ("producer", "auditor", "buyer", "issuer", "supplier", "manufacturer", "regulator")}
    names = ["node-1", "node-2", "node-3", "node-4"]
    node = Node("node-1", names)
    admin = node.identity  # 创世管理员（bootstrap 时授予，见 Node.__init__）

    # 通过「签名治理交易」建立组织与成员角色，演示真实治理路径，而非直接 grant()。
    admin_nonce = 1
    for role, identity in identities.items():
        org_id = f"org-{role}"
        node.append_block([Transaction.signed(
            admin, f"gov-org-{role}", admin_nonce, "governance_register_organization",
            {"organization_id": org_id, "name": f"{role} Org"})])
        admin_nonce += 1
        node.append_block([Transaction.signed(
            admin, f"gov-member-{role}", admin_nonce, "governance_add_member",
            {"organization_id": org_id, "address": identity.address, "role": role})])
        admin_nonce += 1

    txs = [
        Transaction.signed(
            identities["producer"], "create-001", 1, "create_product",
            {"product_id": "GT-001", "carbon_kg": 12.5}),
        Transaction.signed(
            identities["auditor"], "audit-001", 1, "audit_product",
            {"product_id": "GT-001", "passed": True}),
    ]
    step = 0

    def run(label, tx):
        nonlocal step
        step += 1
        print(f"\n步骤 {step:02d} | 参与方={tx.sender[:12]} | {label}")
        try:
            block = node.append_block([tx])
            print(f"  回执: committed tx={tx.tx_hash()[:16]} block={block.height}")
            print("  状态:", node.trace_for_request(tx.request_id)[-1].after)
        except ValueError as exc:
            print(f"  回执: rejected reason={exc}")
        print("  trace:", [event.stage for event in node.trace_for_request(tx.request_id)])

    for tx in txs:
        run(tx.action, tx)
    run("mint_tokens", Transaction.signed(
        identities["issuer"], "mint-001", 1, "mint_tokens",
        {"token": "CarbonCoin", "to": identities["buyer"].address, "amount": 100}
    ))
    run("create_order", Transaction.signed(
        identities["buyer"], "order-001", 1, "create_order",
        {"order_id": "ORDER-001", "supplier": identities["supplier"].address,
         "manufacturer": identities["manufacturer"].address,
         "buyer": identities["buyer"].address, "amount": 25, "token": "CarbonCoin"}
    ))
    run("acknowledge_order", Transaction.signed(
        identities["supplier"], "ack-001", 1, "acknowledge_order",
        {"order_id": "ORDER-001"}
    ))
    run("deliver_order", Transaction.signed(
        identities["supplier"], "deliver-001", 2, "deliver_order",
        {"order_id": "ORDER-001", "on_time": True}
    ))
    run("partial_accept", Transaction.signed(
        identities["buyer"], "accept-001", 2, "partial_accept",
        {"order_id": "ORDER-001", "amount": 10}
    ))
    run("dispute_order", Transaction.signed(
        identities["buyer"], "dispute-001", 3, "dispute_order",
        {"order_id": "ORDER-001", "reason": "quality mismatch",
         "evidence_hash": "sha256:demo-evidence"}
    ))
    run("resolve_dispute(refund)", Transaction.signed(
        identities["regulator"], "resolve-001", 1, "resolve_dispute",
        {"order_id": "ORDER-001", "decision": "refund",
         "reason": "verified quality mismatch"}
    ))
    run("失败示例（重复订单）", Transaction.signed(
        identities["buyer"], "order-rejected", 4, "create_order",
        {"order_id": "ORDER-001", "supplier": identities["supplier"].address,
         "manufacturer": identities["manufacturer"].address, "amount": 25}
    ))
    print("\n查询结果")
    print("  订单 timeline:")
    pprint([e.model_dump() for e in node.order_timeline("ORDER-001")])
    print("  买方资金流:")
    pprint(node.funds_flow(identities["buyer"].address))
    print("  订单状态:", node.contract.orders["ORDER-001"]["status"])
    print("  买方 CarbonCoin:", node.contract.balance(identities["buyer"].address))


if __name__ == "__main__":
    main()
