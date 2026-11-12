"""Контракт банка: формат ошибки задаётся и разбирается в одном модуле."""

import pytest

from handoff_bench.billing.protocol import format_error, parse_error


def test_format_then_parse_roundtrip() -> None:
    text = format_error("timeout", "Request to billing timed out; the outcome is unknown.")
    assert parse_error("create_charge", text) == (
        "timeout",
        "Request to billing timed out; the outcome is unknown.",
    )


def test_parse_strips_mcp_sdk_prefix() -> None:
    text = "Error executing tool get_invoice: " + format_error("invoice_not_found", "No INV-9.")
    assert parse_error("get_invoice", text) == ("invoice_not_found", "No INV-9.")


@pytest.mark.parametrize("text", ["boom", "not a code: message", ": empty code"])
def test_foreign_text_is_not_parsed(text: str) -> None:
    assert parse_error("get_invoice", text) is None
