#!/usr/bin/env python3
"""
Migrate card data from English to Portuguese column keys.

Run once after deployment to migrate existing cards.
Safe to run multiple times (idempotent).
"""
import os
import sys
import json
import redis

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

MIGRATION_MAP = {
    "Backlog": "Espera",
    "In Progress": "Produção",
    "Waiting Approval": "Aprovação",
    "Scheduled": "Aprovação",  # Scheduled cards merge into Aprovação
    "Published": "Finalizado"
}

def migrate():
    """Migrate cards from English to Portuguese keys."""
    r = redis.Redis.from_url(REDIS_URL, decode_responses=True)

    print("🔄 Starting column name migration...")
    print(f"📍 Redis: {REDIS_URL}")

    total_migrated = 0

    for en_col, pt_col in MIGRATION_MAP.items():
        en_key = f"wf:board:col:{en_col}"
        pt_key = f"wf:board:col:{pt_col}"

        # Get all cards from English key
        cards = r.lrange(en_key, 0, -1)

        if not cards:
            print(f"  ✓ {en_col}: no cards to migrate")
            continue

        print(f"  🔄 {en_col} → {pt_col}: {len(cards)} cards")

        # Copy each card to Portuguese key
        for card_json in cards:
            try:
                card = json.loads(card_json)
                # Update status field to Portuguese
                card["status"] = pt_col
                # Write to Portuguese key
                r.lpush(pt_key, json.dumps(card))
                total_migrated += 1
            except Exception as e:
                print(f"    ⚠️  Error migrating card: {e}")

        # Optional: Delete English key after successful migration
        # Commented out for safety - uncomment after verification
        # r.delete(en_key)
        # print(f"    🗑️  Deleted English key: {en_key}")

    print(f"\n✅ Migration complete: {total_migrated} cards migrated")
    print(f"⚠️  English keys NOT deleted (for safety)")
    print(f"   To delete English keys after verification, uncomment deletion code")

if __name__ == "__main__":
    migrate()
