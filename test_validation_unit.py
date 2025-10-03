#!/usr/bin/env python3
"""
Direct unit test for tool validation functions.

Since the LLM is intelligent enough to avoid invalid tool calls,
we need to test the validation layer directly to ensure it works correctly.
"""
import sys
sys.path.insert(0, '/home/ubuntu')

from src.api.chat import validate_tool_call, extract_valid_enum_values, format_validation_error_pt
from pydantic import ValidationError

def test_move_card_invalid_column():
    """Test that invalid column value is rejected."""
    tool_call = {
        "name": "move_card",
        "input": {
            "card_ref": "c-test123",
            "to": "APROVACAO"  # Missing accent, wrong case
        }
    }

    is_valid, error_msg = validate_tool_call(tool_call)

    assert not is_valid, "Expected validation to fail for invalid column"
    assert error_msg is not None, "Expected error message"
    assert "Valor inválido" in error_msg, f"Expected 'Valor inválido' in error, got: {error_msg}"
    assert "Aprovação" in error_msg, f"Expected 'Aprovação' in suggestions, got: {error_msg}"

    print("✅ TEST 1 PASSED: Invalid column rejected")
    print(f"   Error message: {error_msg}")
    return True

def test_move_card_valid_column():
    """Test that valid column value is accepted."""
    tool_call = {
        "name": "move_card",
        "input": {
            "card_ref": "c-test456",
            "to": "Aprovação"  # Correct case and accent
        }
    }

    is_valid, error_msg = validate_tool_call(tool_call)

    assert is_valid, f"Expected validation to pass, but got error: {error_msg}"
    assert error_msg is None, f"Expected no error message, got: {error_msg}"

    print("✅ TEST 2 PASSED: Valid column accepted")
    return True

def test_move_card_lowercase():
    """Test that lowercase column value is rejected."""
    tool_call = {
        "name": "move_card",
        "input": {
            "card_ref": "c-test789",
            "to": "produção"  # Wrong case
        }
    }

    is_valid, error_msg = validate_tool_call(tool_call)

    assert not is_valid, "Expected validation to fail for lowercase column"
    assert "Produção" in error_msg, f"Expected 'Produção' in suggestions, got: {error_msg}"

    print("✅ TEST 3 PASSED: Lowercase column rejected")
    print(f"   Error message: {error_msg}")
    return True

def test_create_card_invalid_column():
    """Test that invalid column in create_card is rejected."""
    tool_call = {
        "name": "create_card",
        "input": {
            "title": "Test Card",
            "column": "PRODUCAO"  # Missing accent
        }
    }

    is_valid, error_msg = validate_tool_call(tool_call)

    assert not is_valid, "Expected validation to fail for invalid column"
    assert "Produção" in error_msg, f"Expected 'Produção' in suggestions, got: {error_msg}"

    print("✅ TEST 4 PASSED: Invalid create_card column rejected")
    print(f"   Error message: {error_msg}")
    return True

def test_missing_required_field():
    """Test that missing required field is caught."""
    tool_call = {
        "name": "move_card",
        "input": {
            "card_ref": "c-test123"
            # Missing 'to' field
        }
    }

    is_valid, error_msg = validate_tool_call(tool_call)

    assert not is_valid, "Expected validation to fail for missing field"
    assert "obrigatório" in error_msg.lower() or "to" in error_msg, f"Expected field error, got: {error_msg}"

    print("✅ TEST 5 PASSED: Missing required field detected")
    print(f"   Error message: {error_msg}")
    return True

def test_title_too_long():
    """Test that title exceeding 140 chars is rejected."""
    long_title = "x" * 141  # 141 characters
    tool_call = {
        "name": "create_card",
        "input": {
            "title": long_title
        }
    }

    is_valid, error_msg = validate_tool_call(tool_call)

    assert not is_valid, "Expected validation to fail for long title"
    assert "longo" in error_msg.lower() or "140" in error_msg, f"Expected length error, got: {error_msg}"

    print("✅ TEST 6 PASSED: Long title detected")
    print(f"   Error message: {error_msg}")
    return True

def test_extract_valid_enum_values():
    """Test enum value extraction helper."""
    values = extract_valid_enum_values("move_card", "to")

    assert values == ["Espera", "Produção", "Aprovação", "Finalizado"], \
        f"Expected correct enum values, got: {values}"

    print("✅ TEST 7 PASSED: Enum value extraction works")
    return True

def main():
    """Run all validation tests."""
    print("╔═══════════════════════════════════════════════════════════╗")
    print("║      Tool Validation Unit Tests                          ║")
    print("╚═══════════════════════════════════════════════════════════╝")
    print()

    tests = [
        ("Invalid column value (APROVACAO)", test_move_card_invalid_column),
        ("Valid column value (Aprovação)", test_move_card_valid_column),
        ("Lowercase column value (produção)", test_move_card_lowercase),
        ("Invalid create_card column (PRODUCAO)", test_create_card_invalid_column),
        ("Missing required field", test_missing_required_field),
        ("Title too long (>140 chars)", test_title_too_long),
        ("Extract valid enum values", test_extract_valid_enum_values),
    ]

    passed = 0
    failed = 0

    for test_name, test_func in tests:
        print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"TEST: {test_name}")
        print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        try:
            test_func()
            passed += 1
        except AssertionError as e:
            print(f"❌ FAILED: {e}")
            failed += 1
        print()

    print("╔═══════════════════════════════════════════════════════════╗")
    print("║                    SUMMARY                                ║")
    print("╚═══════════════════════════════════════════════════════════╝")
    print()
    print(f"✅ Passed: {passed}/{len(tests)}")
    print(f"❌ Failed: {failed}/{len(tests)}")
    print()

    if failed == 0:
        print("🎉 All validation tests PASSED!")
        print()
        print("Validation Layer Status:")
        print("  ✅ Enum validation (column names)")
        print("  ✅ Required field validation")
        print("  ✅ String length validation")
        print("  ✅ PT-BR error messages")
        print("  ✅ Helpful suggestions for enum errors")
        return 0
    else:
        print("❌ Some tests failed. Please review the errors above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
