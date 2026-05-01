"""Sanity tests for ``soc_claw.logging_config``.

Verify that ``setup_logging()`` produces parseable JSON lines and that
``extra={}`` fields land as top-level keys, since aggregator queries
depend on that contract.
"""

import json
import logging

from soc_claw.logging_config import setup_logging


def test_emits_json_lines(capsys):
    setup_logging()
    logging.getLogger("soc-claw").info(
        "routing_decision",
        extra={"event": "routing_decision", "agent": "triage", "route": "local"},
    )
    err = capsys.readouterr().err.strip().splitlines()[-1]
    parsed = json.loads(err)
    assert parsed["message"] == "routing_decision"
    assert parsed["agent"] == "triage"
    assert parsed["route"] == "local"
    assert "timestamp" in parsed
    assert "level" in parsed


def test_trace_context_filter_no_active_span(capsys):
    """Without an active span, log line should still parse cleanly."""
    setup_logging()
    logging.getLogger("soc-claw").info("plain")
    parsed = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert "trace_id" not in parsed


def test_log_file_env_var(tmp_path, monkeypatch):
    """SOC_CLAW_LOG_FILE redirects JSON output to a file in append mode."""
    log_path = tmp_path / "soc-claw.jsonl"
    monkeypatch.setenv("SOC_CLAW_LOG_FILE", str(log_path))
    setup_logging()
    logging.getLogger("soc-claw").info(
        "to_file",
        extra={"event": "to_file", "agent": "triage"},
    )

    # Force handler flush
    for h in logging.getLogger().handlers:
        h.flush()

    assert log_path.exists()
    last_line = log_path.read_text().strip().splitlines()[-1]
    parsed = json.loads(last_line)
    assert parsed["message"] == "to_file"
    assert parsed["agent"] == "triage"


def test_log_level_env_var(monkeypatch, capsys):
    """SOC_CLAW_LOG_LEVEL=WARNING suppresses INFO records."""
    monkeypatch.setenv("SOC_CLAW_LOG_LEVEL", "WARNING")
    setup_logging()
    logger = logging.getLogger("soc-claw")
    logger.info("should_be_suppressed")
    logger.warning("should_appear")
    err_lines = [
        line for line in capsys.readouterr().err.strip().splitlines() if line
    ]
    messages = [json.loads(line)["message"] for line in err_lines]
    assert "should_be_suppressed" not in messages
    assert "should_appear" in messages
