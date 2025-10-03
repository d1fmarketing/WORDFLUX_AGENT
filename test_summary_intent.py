#!/usr/bin/env python3
"""Test script for summary intent detection and forced tool calling."""

import re
from typing import Dict, Any

# Inline implementation for testing without FastAPI dependencies
def check_summary_intent(user_message: str) -> bool:
    """Detect if user's message is requesting a board summary."""
    text = user_message.lower()

    # Exclude card creation/modification commands
    creation_patterns = [
        r'^\s*(crie|criar|cria|adicione|adicionar|adiciona|mova|mover|move|atualize|atualizar|atualiza)\b'
    ]
    for pattern in creation_patterns:
        if re.search(pattern, text):
            return False  # Exclude creation commands

    summary_patterns = [
        r'\bresumo\b',
        r'\bquantos?\b',
        r'\bo que temos\b',
        r'\bpara hoje\b',
        r'\bstatus\b',
        r'\boverview\b',
        r'\btotal\b',
        r'\bvisão geral\b',
        r'\bsumário\b',
        r'\bpanorama\b'
    ]
    for pattern in summary_patterns:
        if re.search(pattern, text):
            return True
    return False

def format_summary_response(summary: Dict[str, Any]) -> str:
    """Format summarize_board result as concise PT-BR response."""
    totals = summary.get("totals_por_coluna", {})
    total = summary.get("contagem_total", 0)
    prazos = summary.get("prazos_proximos", [])
    gargalos = summary.get("gargalos", [])
    error = summary.get("error")

    if error:
        return f"⚠️ {error}"

    lines = [f"📊 **Total: {total} cards**"]

    if totals:
        for col, count in totals.items():
            lines.append(f"• {col}: {count} cards")
    else:
        lines.append("• Nenhum card no board")

    if prazos:
        lines.append(f"\n⏰ **{len(prazos)} cards com prazo próximo** (próximos 7 dias)")

    if gargalos:
        lines.append(f"\n⚠️ **Gargalos**: {', '.join(gargalos)}")

    return "\n".join(lines)

# Test cases for intent detection
test_messages = [
    # Should detect (True)
    ("quantos cards temos?", True),
    ("quantos cards no total?", True),
    ("resumo do board", True),
    ("me dá um resumo", True),
    ("qual o status?", True),
    ("status do board", True),
    ("o que temos hoje?", True),
    ("o que temos para hoje?", True),
    ("total de cards", True),
    ("overview", True),
    ("visão geral", True),

    # Should NOT detect (False)
    ("crie um card de resumo", False),
    ("adicione uma tarefa", False),
    ("mova o card para produção", False),
    ("assim não funciona", False),  # "sim" within "assim"
]

print("=" * 70)
print("TEST 1: Intent Detection")
print("=" * 70)

passed = 0
failed = 0

for message, expected in test_messages:
    result = check_summary_intent(message)
    status = "✅ PASS" if result == expected else "❌ FAIL"

    if result == expected:
        passed += 1
    else:
        failed += 1

    print(f"{status}: '{message}' → {result} (expected {expected})")

print(f"\nResults: {passed} passed, {failed} failed\n")

# Test formatting
print("=" * 70)
print("TEST 2: Response Formatting")
print("=" * 70)

# Test case 1: Normal summary
summary1 = {
    "totals_por_coluna": {
        "Espera": 5,
        "Produção": 2,
        "Aprovação": 3,
        "Finalizado": 10
    },
    "contagem_total": 20,
    "prazos_proximos": [
        {"title": "Card A", "due_date": "2025-10-05", "column": "Produção"},
        {"title": "Card B", "due_date": "2025-10-06", "column": "Aprovação"}
    ],
    "gargalos": [
        "Produção (2/2 cards - limite WIP)",
        "Aprovação (1 cards >7 dias)"
    ]
}

formatted1 = format_summary_response(summary1)
print("Test case 1: Full summary with deadlines and bottlenecks")
print(formatted1)
print()

# Test case 2: Empty board
summary2 = {
    "totals_por_coluna": {},
    "contagem_total": 0,
    "prazos_proximos": [],
    "gargalos": []
}

formatted2 = format_summary_response(summary2)
print("Test case 2: Empty board")
print(formatted2)
print()

# Test case 3: Error case
summary3 = {
    "totals_por_coluna": {},
    "contagem_total": 0,
    "prazos_proximos": [],
    "gargalos": [],
    "error": "Não foi possível acessar o estado do board"
}

formatted3 = format_summary_response(summary3)
print("Test case 3: Error")
print(formatted3)
print()

print("=" * 70)
print("All tests completed!")
print("=" * 70)
