"""Tests for SIEM mappers."""

import pytest
from blue_lantern.connectors.siem_splunk import SplunkMapper
from blue_lantern.connectors.siem_sentinel import SentinelMapper
from blue_lantern.connectors.siem_crowdstrike import CrowdStrikeMapper
from blue_lantern.connectors.base import NormalizationError


class TestSplunkMapper:
    """Tests for Splunk mapper."""

    @pytest.fixture
    def mapper(self):
        return SplunkMapper()

    def test_normalize_valid_event(self, mapper):
        """Test normalization of valid Splunk event."""
        raw_event = {
            "_time": "2026-04-25T14:32:00Z",
            "_raw": "powershell.exe -enc ...",
            "source": "WinEventLog:Security",
            "sourcetype": "WinEventLog:Security",
            "host": "DC-FINANCE-01",
            "result": {
                "source_ip": "10.0.4.17",
                "dest_ip": "185.220.101.42",
                "alert_id": "ALT-001",
            },
        }

        alert = mapper.normalize(raw_event)

        assert alert["id"] == "ALT-001"
        assert alert["timestamp"] == "2026-04-25T14:32:00Z"
        assert alert["hostname"] == "DC-FINANCE-01"
        assert alert["rule_name"] == "WinEventLog:Security"
        assert alert["source_ip"] == "10.0.4.17"
        assert alert["dest_ip"] == "185.220.101.42"
        assert alert["payload"] == "powershell.exe -enc ..."
        assert "ground_truth" not in alert

    def test_normalize_missing_required_field(self, mapper):
        """Test normalization fails with missing required field."""
        raw_event = {
            "_time": "2026-04-25T14:32:00Z",
            "_raw": "powershell.exe -enc ...",
            "source": "WinEventLog:Security",
            "sourcetype": "WinEventLog:Security",
            "host": "DC-FINANCE-01",
        }

        with pytest.raises(NormalizationError) as exc_info:
            mapper.normalize(raw_event)

        assert "Missing required fields" in str(exc_info.value)

    def test_normalize_with_ground_truth(self, mapper):
        """Test that ground_truth is stripped."""
        raw_event = {
            "_time": "2026-04-25T14:32:00Z",
            "_raw": "powershell.exe -enc ...",
            "source": "WinEventLog:Security",
            "sourcetype": "WinEventLog:Security",
            "host": "DC-FINANCE-01",
            "result": {"alert_id": "ALT-001"},
            "ground_truth": {"severity": "P1", "is_malicious": True},
        }

        alert = mapper.normalize(raw_event)

        assert "ground_truth" not in alert

    def test_extract_source(self, mapper):
        """Test source extraction."""
        raw_event = {"_time": "2026-04-25T14:32:00Z"}
        source = mapper.extract_source(raw_event)
        assert source == "splunk"


class TestSentinelMapper:
    """Tests for Sentinel mapper."""

    @pytest.fixture
    def mapper(self):
        return SentinelMapper()

    def test_normalize_valid_event(self, mapper):
        """Test normalization of valid Sentinel event."""
        raw_event = {
            "properties": {
                "alertDisplayName": "Suspicious PowerShell Activity",
                "startTimeUtc": "2026-04-25T14:32:00Z",
            },
            "systemAlertId": "ALT-002",
            "entities": [
                {
                    "kind": "Host",
                    "properties": {"hostName": "DC-CORP-01"},
                },
                {
                    "kind": "Ip",
                    "properties": {"address": "10.0.2.50"},
                },
            ],
        }

        alert = mapper.normalize(raw_event)

        assert alert["id"] == "ALT-002"
        assert alert["timestamp"] == "2026-04-25T14:32:00Z"
        assert alert["hostname"] == "DC-CORP-01"
        assert alert["rule_name"] == "Suspicious PowerShell Activity"
        assert alert["source_ip"] == "10.0.2.50"

    def test_extract_source(self, mapper):
        """Test source extraction."""
        raw_event = {"systemAlertId": "ALT-002"}
        source = mapper.extract_source(raw_event)
        assert source == "sentinel"


class TestCrowdStrikeMapper:
    """Tests for CrowdStrike mapper."""

    @pytest.fixture
    def mapper(self):
        return CrowdStrikeMapper()

    def test_normalize_valid_event(self, mapper):
        """Test normalization of valid CrowdStrike event."""
        raw_event = {
            "detection_id": "ALT-003",
            "timestamp": "2026-04-25T14:32:00Z",
            "severity": "critical",
            "composite": {
                "hostname": "WS-HR-01",
                "source_ip": "10.0.3.100",
            },
        }

        alert = mapper.normalize(raw_event)

        assert alert["id"] == "ALT-003"
        assert alert["timestamp"] == "2026-04-25T14:32:00Z"
        assert alert["hostname"] == "WS-HR-01"
        assert alert["source_ip"] == "10.0.3.100"
        assert alert["severity"] == "P1"

    def test_severity_mapping(self, mapper):
        """Test severity mapping."""
        test_cases = [
            ("critical", "P1"),
            ("high", "P2"),
            ("medium", "P3"),
            ("low", "P4"),
            ("unknown", "P3"),  # Default
        ]

        for cs_severity, expected_prio in test_cases:
            raw_event = {
                "detection_id": "ALT-003",
                "timestamp": "2026-04-25T14:32:00Z",
                "severity": cs_severity,
                "composite": {"hostname": "WS-HR-01"},
            }
            alert = mapper.normalize(raw_event)
            assert alert["severity"] == expected_prio

    def test_extract_source(self, mapper):
        """Test source extraction."""
        raw_event = {"detection_id": "ALT-003"}
        source = mapper.extract_source(raw_event)
        assert source == "crowdstrike"
