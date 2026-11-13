"""PY-M07 tests — LLM explanation layer.

All tests are pure-unit (no DB, no network, no real LLM calls).
LLMExplainer DB integration tests use the requires_db marker.

Coverage:
  - LLMReport schema: valid input, validators, edge cases
  - DisabledProvider: always returns None, is_available=False
  - OpenAIProvider: HTTP 200 success, 4xx error, timeout, parse error,
    invalid JSON, Pydantic validation failure
  - PromptBuilder: snapshot sanitisation, IP pseudonymisation,
    sensitive key stripping, prompt structure
  - LLMExplainer: disabled provider returns None, evidence_ids validated,
    invalid refs stripped, report persisted with model_name+prompt_version
  - build_provider factory: disabled, openai with key, openai without key
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.intelligence.llm.base import LLMReport, ObservedFact, PossibleExplanation
from app.intelligence.llm.disabled import DisabledProvider
from app.intelligence.llm.prompt_builder import (
    PROMPT_VERSION,
    _is_sensitive_key,
    _pseudonymise_ip,
    _sanitise_snapshot,
    build_system_prompt,
    build_user_prompt,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DB_URL = os.environ.get(
    "INVESTIGATION_DATABASE_URL",
    "postgresql+asyncpg://investigation:local_password@127.0.0.1:5437/agio_investigation",
)


def _uid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _valid_llm_report_dict(**overrides) -> dict:
    base = {
        "summary": "The deposit amount differs from the expected amount.",
        "observed_facts": [
            {
                "statement": "Received 0.95 USDT, expected 1.00 USDT.",
                "evidence_ids": ["ev-uuid-001"],
            }
        ],
        "possible_explanations": [
            {
                "explanation": "The customer may have sent a partial deposit.",
                "confidence": "low",
            }
        ],
        "missing_evidence": ["Provider settlement confirmation"],
        "recommended_review": [
            "Review the blockchain transaction and underpayment policy."
        ],
        "limitations": ["This report does not determine customer intent."],
    }
    base.update(overrides)
    return base


# ===========================================================================
# LLMReport schema
# ===========================================================================


class TestLLMReport:
    def test_valid_report(self):
        r = LLMReport.model_validate(_valid_llm_report_dict())
        assert r.summary == "The deposit amount differs from the expected amount."
        assert len(r.observed_facts) == 1
        assert len(r.possible_explanations) == 1
        assert r.possible_explanations[0].confidence == "low"

    def test_empty_summary_rejected(self):
        with pytest.raises(ValidationError):
            LLMReport.model_validate(_valid_llm_report_dict(summary="   "))

    def test_whitespace_only_summary_rejected(self):
        with pytest.raises(ValidationError):
            LLMReport.model_validate(_valid_llm_report_dict(summary="\n\t"))

    def test_summary_stripped(self):
        r = LLMReport.model_validate(_valid_llm_report_dict(summary="  hello  "))
        assert r.summary == "hello"

    def test_invalid_confidence_rejected(self):
        with pytest.raises(ValidationError):
            LLMReport.model_validate(_valid_llm_report_dict(
                possible_explanations=[{"explanation": "test", "confidence": "maybe"}]
            ))

    def test_all_confidence_values_valid(self):
        for confidence in ("low", "medium", "high"):
            r = LLMReport.model_validate(_valid_llm_report_dict(
                possible_explanations=[{"explanation": "test", "confidence": confidence}]
            ))
            assert r.possible_explanations[0].confidence == confidence

    def test_empty_lists_valid(self):
        r = LLMReport.model_validate(_valid_llm_report_dict(
            observed_facts=[],
            possible_explanations=[],
            missing_evidence=[],
            recommended_review=[],
            limitations=[],
        ))
        assert r.observed_facts == []

    def test_observed_fact_no_evidence_ids_valid(self):
        """Facts with empty evidence_ids are accepted (validator warns but allows)."""
        r = LLMReport.model_validate(_valid_llm_report_dict(
            observed_facts=[{"statement": "Some fact", "evidence_ids": []}]
        ))
        assert r.observed_facts[0].evidence_ids == []


# ===========================================================================
# DisabledProvider
# ===========================================================================


class TestDisabledProvider:
    def test_is_not_available(self):
        p = DisabledProvider()
        assert p.is_available() is False

    def test_provider_name(self):
        assert DisabledProvider().provider_name == "disabled"

    async def test_explain_returns_none(self):
        p = DisabledProvider()
        result = await p.explain("sys", "user", model="gpt-4o-mini")
        assert result is None


# ===========================================================================
# OpenAIProvider
# ===========================================================================


class TestOpenAIProvider:
    def _make_provider(self):
        from app.intelligence.llm.openai_provider import OpenAIProvider
        return OpenAIProvider(api_key="sk-test-key", timeout_seconds=10)

    def test_is_available(self):
        p = self._make_provider()
        assert p.is_available() is True

    def test_no_key_not_available(self):
        from app.intelligence.llm.openai_provider import OpenAIProvider
        p = OpenAIProvider(api_key="")
        assert p.is_available() is False

    def test_provider_name(self):
        assert self._make_provider().provider_name == "openai"

    async def test_explain_success(self):
        from app.intelligence.llm.openai_provider import OpenAIProvider
        import httpx

        llm_dict = _valid_llm_report_dict()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": json.dumps(llm_dict)}}]
        }

        provider = OpenAIProvider(api_key="sk-test", timeout_seconds=10)
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await provider.explain("sys", "user", model="gpt-4o-mini")

        assert result is not None
        assert isinstance(result, LLMReport)
        assert result.summary == llm_dict["summary"]

    async def test_explain_4xx_returns_none(self):
        from app.intelligence.llm.openai_provider import OpenAIProvider

        mock_response = MagicMock()
        mock_response.status_code = 401

        provider = OpenAIProvider(api_key="sk-bad", timeout_seconds=10)
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await provider.explain("sys", "user", model="gpt-4o-mini")

        assert result is None

    async def test_explain_timeout_returns_none(self):
        import httpx
        from app.intelligence.llm.openai_provider import OpenAIProvider

        provider = OpenAIProvider(api_key="sk-test", timeout_seconds=1)
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
            mock_client_cls.return_value = mock_client

            result = await provider.explain("sys", "user", model="gpt-4o-mini")

        assert result is None

    async def test_explain_invalid_json_returns_none(self):
        from app.intelligence.llm.openai_provider import OpenAIProvider

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "not-json!!{"}}]
        }

        provider = OpenAIProvider(api_key="sk-test", timeout_seconds=10)
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await provider.explain("sys", "user", model="gpt-4o-mini")

        assert result is None

    async def test_explain_invalid_schema_returns_none(self):
        """When JSON is valid but doesn't match LLMReport schema → None."""
        from app.intelligence.llm.openai_provider import OpenAIProvider

        mock_response = MagicMock()
        mock_response.status_code = 200
        # Missing required 'summary' field
        mock_response.json.return_value = {
            "choices": [{"message": {"content": json.dumps({"foo": "bar"})}}]
        }

        provider = OpenAIProvider(api_key="sk-test", timeout_seconds=10)
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await provider.explain("sys", "user", model="gpt-4o-mini")

        assert result is None

    async def test_no_api_key_returns_none(self):
        from app.intelligence.llm.openai_provider import OpenAIProvider
        provider = OpenAIProvider(api_key="")
        result = await provider.explain("sys", "user", model="gpt-4o-mini")
        assert result is None


# ===========================================================================
# PromptBuilder
# ===========================================================================


class TestSensitiveKeyDetection:
    def test_password_detected(self):
        assert _is_sensitive_key("password") is True
        assert _is_sensitive_key("user_password") is True

    def test_token_detected(self):
        assert _is_sensitive_key("remember_token") is True
        assert _is_sensitive_key("access_token") is True

    def test_secret_detected(self):
        assert _is_sensitive_key("tatum_webhook_secret") is True
        assert _is_sensitive_key("api_secret") is True

    def test_safe_keys_not_detected(self):
        for k in ("status", "amount", "tx_hash", "created_at", "user_id", "uuid"):
            assert _is_sensitive_key(k) is False, f"'{k}' should not be sensitive"


class TestIPPseudonymisation:
    def test_ipv4_last_octet_replaced(self):
        result = _pseudonymise_ip("192.168.1.100")
        assert result == "192.168.1.xxx"

    def test_no_ip_unchanged(self):
        assert _pseudonymise_ip("no ip here") == "no ip here"

    def test_multiple_ips(self):
        result = _pseudonymise_ip("from 10.0.0.1 to 172.16.0.5")
        assert "10.0.0.xxx" in result
        assert "172.16.0.xxx" in result
        assert ".1" not in result.replace("10.0.", "").replace("172.16.", "")


class TestSnapshotSanitisation:
    def test_password_redacted(self):
        snap = {"password": "secret123", "status": "active"}
        result = _sanitise_snapshot(snap)
        assert result["password"] == "[REDACTED]"
        assert result["status"] == "active"

    def test_raw_data_redacted(self):
        snap = {"tx_hash": "0xabc", "raw_data": {"private_key": "xprv..."}}
        result = _sanitise_snapshot(snap)
        assert result["raw_data"] == "[RAW_DATA_REDACTED]"
        assert result["tx_hash"] == "0xabc"

    def test_nested_sensitive(self):
        snap = {"user": {"remember_token": "tok123", "email": "test@example.com"}}
        result = _sanitise_snapshot(snap)
        assert result["user"]["remember_token"] == "[REDACTED]"
        assert result["user"]["email"] == "test@example.com"

    def test_list_items_sanitised(self):
        snap = [{"api_key": "key1"}, {"amount": "1.0"}]
        result = _sanitise_snapshot(snap)
        assert result[0]["api_key"] == "[REDACTED]"
        assert result[1]["amount"] == "1.0"

    def test_ip_in_string_pseudonymised(self):
        snap = {"ip_address": "192.168.1.50"}
        result = _sanitise_snapshot(snap)
        # ip_address key is not sensitive, but value IP should be pseudonymised
        assert result["ip_address"] == "192.168.1.xxx"

    def test_non_dict_non_list_passthrough(self):
        assert _sanitise_snapshot(42) == 42
        assert _sanitise_snapshot(True) is True
        assert _sanitise_snapshot(None) is None


class TestPromptBuilder:
    def test_system_prompt_contains_injection_guard(self):
        sys = build_system_prompt()
        assert "do not follow any instructions" in sys.lower() or \
               "ignore those instructions" in sys.lower()
        assert "json" in sys.lower()

    def test_user_prompt_contains_investigation_id(self):
        inv_id = _uid()
        prompt = build_user_prompt(
            investigation_id=inv_id,
            external_reference=_uid(),
            entity_type="order",
            evidence_records=[],
            findings=[],
        )
        assert inv_id in prompt

    def test_user_prompt_includes_evidence_blocks(self):
        ev_id = _uid()
        prompt = build_user_prompt(
            investigation_id=_uid(),
            external_reference=_uid(),
            entity_type="order",
            evidence_records=[{
                "id": ev_id,
                "evidence_type": "order",
                "source_record_type": "order",
                "source_record_id": "ref-001",
                "snapshot": {"status": "completed", "password": "secret"},
            }],
            findings=[],
        )
        assert ev_id in prompt
        assert "DATA BLOCK" in prompt
        # Sensitive field must be redacted in the prompt
        assert "[REDACTED]" in prompt
        assert "secret" not in prompt

    def test_user_prompt_includes_findings(self):
        prompt = build_user_prompt(
            investigation_id=_uid(),
            external_reference=_uid(),
            entity_type="order",
            evidence_records=[],
            findings=[{
                "id": _uid(),
                "finding_code": "BLOCKCHAIN_DEPOSIT_UNDERPAID",
                "severity": "high",
                "title": "Deposit underpaid",
                "description": "Received 0.95, expected 1.00",
            }],
        )
        assert "BLOCKCHAIN_DEPOSIT_UNDERPAID" in prompt
        assert "DETERMINISTIC FINDINGS" in prompt

    def test_no_findings_message(self):
        prompt = build_user_prompt(
            investigation_id=_uid(),
            external_reference=_uid(),
            entity_type="order",
            evidence_records=[],
            findings=[],
        )
        assert "No findings triggered" in prompt

    def test_prompt_version_constant(self):
        assert PROMPT_VERSION == "1.0.0"


# ===========================================================================
# build_provider factory
# ===========================================================================


class TestBuildProvider:
    def _settings(self, provider="disabled", api_key=None):
        s = MagicMock()
        s.llm_provider = provider
        s.llm_api_key = api_key
        s.llm_timeout_seconds = 10
        return s

    def test_disabled_returns_disabled(self):
        from app.intelligence.llm.explainer import build_provider
        p = build_provider(self._settings("disabled"))
        assert p.provider_name == "disabled"
        assert p.is_available() is False

    def test_openai_with_key_returns_openai(self):
        from app.intelligence.llm.explainer import build_provider
        p = build_provider(self._settings("openai", api_key="sk-test"))
        assert p.provider_name == "openai"
        assert p.is_available() is True

    def test_openai_without_key_falls_back_to_disabled(self):
        from app.intelligence.llm.explainer import build_provider
        p = build_provider(self._settings("openai", api_key=None))
        assert p.provider_name == "disabled"

    def test_unknown_provider_falls_back_to_disabled(self):
        from app.intelligence.llm.explainer import build_provider
        p = build_provider(self._settings("anthropic"))
        assert p.provider_name == "disabled"


# ===========================================================================
# LLMExplainer unit tests (mocked provider)
# ===========================================================================


class TestLLMExplainerUnit:
    async def test_disabled_provider_returns_none(self):
        from app.intelligence.llm.explainer import LLMExplainer

        session = AsyncMock()
        provider = DisabledProvider()
        explainer = LLMExplainer(provider, session)

        result = await explainer.explain(
            investigation_id=_uid(),
            external_reference=_uid(),
            entity_type="order",
            model="gpt-4o-mini",
        )
        assert result is None

    async def test_provider_returns_none_gracefully(self):
        """When provider returns None (failure), explainer returns None."""
        from app.intelligence.llm.explainer import LLMExplainer

        mock_provider = MagicMock()
        mock_provider.is_available.return_value = True
        mock_provider.provider_name = "mock"
        mock_provider.explain = AsyncMock(return_value=None)

        session = AsyncMock()
        # Mock repository calls
        with patch("app.intelligence.llm.explainer.evidence_record_repository") as mock_ev, \
             patch("app.intelligence.llm.explainer.finding_repository") as mock_fi:
            mock_ev.get_by_investigation = AsyncMock(return_value=[])
            mock_fi.get_by_investigation = AsyncMock(return_value=[])

            explainer = LLMExplainer(mock_provider, session)
            result = await explainer.explain(
                investigation_id=_uid(),
                external_reference=_uid(),
                entity_type="order",
                model="gpt-4o-mini",
            )

        assert result is None

    async def test_invalid_evidence_ids_stripped(self):
        """Evidence_ids not in the investigation are stripped from observed_facts."""
        from app.intelligence.llm.explainer import LLMExplainer

        real_ev_id = _uid()
        fake_ev_id = _uid()  # not in investigation

        # LLM returns a fact referencing one valid + one invalid evidence_id
        llm_output = LLMReport.model_validate({
            **_valid_llm_report_dict(),
            "observed_facts": [
                {"statement": "Deposit underpaid.", "evidence_ids": [real_ev_id, fake_ev_id]}
            ],
        })

        mock_provider = MagicMock()
        mock_provider.is_available.return_value = True
        mock_provider.provider_name = "mock"
        mock_provider.explain = AsyncMock(return_value=llm_output)

        # Build a mock evidence record
        mock_ev_record = MagicMock()
        mock_ev_record.id = real_ev_id
        mock_ev_record.evidence_type = "order"
        mock_ev_record.source_record_type = "order"
        mock_ev_record.source_record_id = "ref-001"
        mock_ev_record.snapshot = {"status": "completed"}

        # Build a mock saved report
        mock_saved_report = MagicMock()
        mock_saved_report.id = _uid()

        session = AsyncMock()

        with patch("app.intelligence.llm.explainer.evidence_record_repository") as mock_ev_repo, \
             patch("app.intelligence.llm.explainer.finding_repository") as mock_fi_repo, \
             patch("app.intelligence.llm.explainer.report_repository") as mock_rp_repo:

            mock_ev_repo.get_by_investigation = AsyncMock(return_value=[mock_ev_record])
            mock_fi_repo.get_by_investigation = AsyncMock(return_value=[])
            mock_rp_repo.next_version = AsyncMock(return_value=2)
            mock_rp_repo.insert = AsyncMock(return_value=mock_saved_report)

            explainer = LLMExplainer(mock_provider, session)
            result = await explainer.explain(
                investigation_id=_uid(),
                external_reference="ref-001",
                entity_type="order",
                model="gpt-4o-mini",
            )

        assert result is not None
        # Verify that report_repository.insert was called
        mock_rp_repo.insert.assert_called_once()
        # Get the InvestigationReport that was passed to insert
        call_args = mock_rp_repo.insert.call_args
        saved_report = call_args[0][1]  # second positional arg
        # The observed_facts should only contain the valid evidence_id
        facts = saved_report.observed_facts
        assert len(facts) == 1
        assert real_ev_id in facts[0]["evidence_ids"]
        assert fake_ev_id not in facts[0]["evidence_ids"]

    async def test_model_name_recorded(self):
        """model_name in report = provider:model format."""
        from app.intelligence.llm.explainer import LLMExplainer

        llm_output = LLMReport.model_validate(_valid_llm_report_dict(observed_facts=[]))
        mock_provider = MagicMock()
        mock_provider.is_available.return_value = True
        mock_provider.provider_name = "openai"
        mock_provider.explain = AsyncMock(return_value=llm_output)

        mock_saved = MagicMock()
        mock_saved.id = _uid()

        session = AsyncMock()
        with patch("app.intelligence.llm.explainer.evidence_record_repository") as mock_ev, \
             patch("app.intelligence.llm.explainer.finding_repository") as mock_fi, \
             patch("app.intelligence.llm.explainer.report_repository") as mock_rp:
            mock_ev.get_by_investigation = AsyncMock(return_value=[])
            mock_fi.get_by_investigation = AsyncMock(return_value=[])
            mock_rp.next_version = AsyncMock(return_value=1)
            mock_rp.insert = AsyncMock(return_value=mock_saved)

            explainer = LLMExplainer(mock_provider, session)
            await explainer.explain(
                investigation_id=_uid(),
                external_reference="ref",
                entity_type="order",
                model="gpt-4o-mini",
            )

        call_args = mock_rp.insert.call_args
        saved = call_args[0][1]
        assert saved.model_name == "openai:gpt-4o-mini"
        assert saved.prompt_version == PROMPT_VERSION

    async def test_llm_limitation_always_added(self):
        """The LLM non-compliance limitation is always appended."""
        from app.intelligence.llm.explainer import LLMExplainer

        llm_output = LLMReport.model_validate(_valid_llm_report_dict(
            limitations=["Custom limitation from LLM."]
        ))
        mock_provider = MagicMock()
        mock_provider.is_available.return_value = True
        mock_provider.provider_name = "openai"
        mock_provider.explain = AsyncMock(return_value=llm_output)

        mock_saved = MagicMock()
        mock_saved.id = _uid()

        session = AsyncMock()
        with patch("app.intelligence.llm.explainer.evidence_record_repository") as mock_ev, \
             patch("app.intelligence.llm.explainer.finding_repository") as mock_fi, \
             patch("app.intelligence.llm.explainer.report_repository") as mock_rp:
            mock_ev.get_by_investigation = AsyncMock(return_value=[])
            mock_fi.get_by_investigation = AsyncMock(return_value=[])
            mock_rp.next_version = AsyncMock(return_value=1)
            mock_rp.insert = AsyncMock(return_value=mock_saved)

            explainer = LLMExplainer(mock_provider, session)
            await explainer.explain(
                investigation_id=_uid(),
                external_reference="ref",
                entity_type="order",
                model="gpt-4o-mini",
            )

        call_args = mock_rp.insert.call_args
        saved = call_args[0][1]
        # Must contain the original limitation AND the mandatory LLM disclaimer
        limitations_text = " ".join(saved.limitations)
        assert "Custom limitation from LLM." in limitations_text
        assert "not a legal" in limitations_text.lower() or \
               "not a" in limitations_text.lower()
