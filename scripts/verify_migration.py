#!/usr/bin/env python3
"""
Verify column name migration is complete.

Checks data consistency across English and Portuguese keys.
Safe to run multiple times.
"""
import os
import sys
import json
import redis
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

MIGRATION_MAP = {
    "Backlog": "Espera",
    "In Progress": "Produção",
    "Waiting Approval": "Aprovação",
    "Scheduled": "Aprovação",  # Scheduled cards merge into Aprovação
    "Published": "Finalizado"
}

def verify():
    """Verify migration consistency."""
    r = redis.Redis.from_url(REDIS_URL, decode_responses=True)

    print("🔍 Verifying column name migration...")
    print(f"📍 Redis: {REDIS_URL}\n")

    stats = defaultdict(lambda: {"en": 0, "pt": 0, "card_ids": set()})
    issues = []

    for en_col, pt_col in MIGRATION_MAP.items():
        en_key = f"wf:board:col:{en_col}"
        pt_key = f"wf:board:col:{pt_col}"

        # Count cards in each key
        en_cards = r.lrange(en_key, 0, -1)
        pt_cards = r.lrange(pt_key, 0, -1)

        stats[pt_col]["en"] = len(en_cards)
        stats[pt_col]["pt"] = len(pt_cards)

        # Track card IDs to detect duplicates
        for card_json in en_cards:
            try:
                card = json.loads(card_json)
                card_id = card.get("id")
                if card_id:
                    stats[pt_col]["card_ids"].add(card_id)
            except Exception:
                pass

        for card_json in pt_cards:
            try:
                card = json.loads(card_json)
                card_id = card.get("id")
                if card_id:
                    if card_id in stats[pt_col]["card_ids"]:
                        issues.append(f"⚠️  Duplicate card {card_id} in {pt_col}")
                    stats[pt_col]["card_ids"].add(card_id)
            except Exception:
                pass

        # Report
        print(f"📊 {pt_col}:")
        print(f"   English key ({en_col}): {stats[pt_col]['en']} cards")
        print(f"   Portuguese key ({pt_col}): {stats[pt_col]['pt']} cards")
        print(f"   Unique IDs: {len(stats[pt_col]['card_ids'])}")

        if stats[pt_col]["en"] > 0 and stats[pt_col]["pt"] == 0:
            issues.append(f"❌ Column {pt_col} has cards in English key but not Portuguese!")

        print()

    # Summary
    print("\n" + "="*60)
    if issues:
        print("⚠️  ISSUES FOUND:")
        for issue in issues:
            print(f"   {issue}")
        print("\n❌ Migration verification FAILED")
        sys.exit(1)
    else:
        total_cards = sum(len(s["card_ids"]) for s in stats.values())
        print(f"✅ Migration verification PASSED")
        print(f"📊 Total unique cards: {total_cards}")
        print(f"💡 Safe to delete English keys if desired")

if __name__ == "__main__":
    verify()
