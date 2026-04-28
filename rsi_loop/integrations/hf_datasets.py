"""
HuggingFace Datasets integration for RSI loop training data upload.

Provides utilities for:
- Scrubbing personal/sensitive information from session data
- Extracting structured data from session JSONL files
- Uploading session data to HuggingFace Hub datasets
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Personal-info scrubbing
# ---------------------------------------------------------------------------

# Compiled regex patterns for performance
_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    # Australian mobile phone numbers  (+614xxxxxxxx, +61 4xx xxx xxx, etc.)
    (
        "au_phone",
        re.compile(r"\+61\s?4[\d\s]{8,11}", re.IGNORECASE),
        "[PHONE_REDACTED]",
    ),
    # Generic phone-ish sequences that start with +614
    (
        "au_phone_alt",
        re.compile(r"\+614\d{8}", re.IGNORECASE),
        "[PHONE_REDACTED]",
    ),
    # Email addresses
    (
        "email",
        re.compile(
            r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.IGNORECASE
        ),
        "[EMAIL_REDACTED]",
    ),
    # Anthropic API keys
    (
        "sk_ant",
        re.compile(r"sk-ant-[a-zA-Z0-9_\-]+", re.IGNORECASE),
        "[API_KEY_REDACTED]",
    ),
    # GitHub / generic token env var values (GH_TOKEN=xxx)
    (
        "gh_token",
        re.compile(r"GH_TOKEN\s*=\s*\S+", re.IGNORECASE),
        "GH_TOKEN=[TOKEN_REDACTED]",
    ),
    # Generic api_key assignments  (api_key = "abc123", api_key: 'xyz')
    (
        "api_key",
        re.compile(
            r"api[_\-]?key\s*[:=]\s*[\"']?[^\s\"',;]+[\"']?", re.IGNORECASE
        ),
        "api_key=[API_KEY_REDACTED]",
    ),
    # Encrypted file paths
    (
        "enc_path",
        re.compile(r"memory/encrypted/[^\s\"']*(?:\.enc)?", re.IGNORECASE),
        "[ENC_PATH_REDACTED]",
    ),
    # Long hex strings that look like tokens / secrets (32+ hex chars)
    # We deliberately exclude 8-4-4-4-12 UUID format here because those are
    # commonly non-sensitive session IDs; anything ≥32 contiguous hex chars is
    # treated as a secret token.
    (
        "hex_token",
        re.compile(r"\b[0-9a-f]{32,}\b", re.IGNORECASE),
        "[HEX_TOKEN_REDACTED]",
    ),
    # Private IP addresses in the 10.0.0.x range
    (
        "ip_10",
        re.compile(r"\b10\.0\.0\.\d{1,3}\b"),
        "[IP_REDACTED]",
    ),
    # Specific server IP block (135.181.157.x)
    (
        "ip_135",
        re.compile(r"\b135\.181\.157\.\d{1,3}\b"),
        "[IP_REDACTED]",
    ),
    # Crypto wallet addresses starting with RD (Ravencoin / similar)
    (
        "wallet",
        re.compile(r"\bRD[a-zA-Z0-9]{25,}\b"),
        "[WALLET_REDACTED]",
    ),
    # Password assignments  (password: "secret", password = 'abc')
    (
        "password",
        re.compile(
            r"password\s*[:=]\s*[\"']?[^\s\"']+[\"']?", re.IGNORECASE
        ),
        "password=[PASSWORD_REDACTED]",
    ),
]


def scrub_personal_info(text: str) -> str:
    """Remove personal / sensitive information from *text*.

    Patterns scrubbed:
    - Australian mobile phone numbers
    - Email addresses (including known personal addresses)
    - API key patterns (sk-ant-*, GH_TOKEN, api_key assignments)
    - Encrypted file paths (memory/encrypted/…)
    - Long hex tokens (≥32 hex chars, excluding UUIDs)
    - Private/server IP addresses
    - Crypto wallet addresses (RD…)
    - Password assignments

    Parameters
    ----------
    text:
        The raw string to scrub.

    Returns
    -------
    str
        The scrubbed string with sensitive data replaced by placeholder tokens.
    """
    if not isinstance(text, str):
        return text

    for _name, pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)

    return text


# ---------------------------------------------------------------------------
# Session extraction
# ---------------------------------------------------------------------------

def _scrub_value(value: Any) -> Any:
    """Recursively scrub personal info from nested dicts / lists / strings."""
    if isinstance(value, str):
        return scrub_personal_info(value)
    if isinstance(value, dict):
        return {k: _scrub_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_value(item) for item in value]
    return value


def extract_session_data(session_dir: str) -> dict:
    """Parse a session directory and return structured training data.

    The session directory is expected to contain one or more ``*.jsonl`` files
    where every line is a JSON object representing a message in the
    conversation.  Supported line schemas:

    * ``{"role": "assistant", "content": [...]}`` — assistant turns which may
      contain ``tool_use`` content blocks.
    * ``{"role": "user", "content": [...]}`` — user / tool-result turns which
      may contain ``tool_result`` content blocks.
    * ``{"role": "user"|"assistant", "content": "<string>"}`` — plain text
      turns.
    * ``{"type": "metadata", ...}`` — optional metadata lines.

    Parameters
    ----------
    session_dir:
        Path to the directory that contains the session JSONL file(s).

    Returns
    -------
    dict
        A dictionary with the following keys:

        ``session_key``
            Inferred from the directory name or a metadata line.
        ``agent_id``
            Agent identifier found in metadata (or ``"unknown"``).
        ``model``
            Model name found in metadata or usage records (or ``"unknown"``).
        ``total_tokens``, ``input_tokens``, ``output_tokens``
            Aggregated token counts from ``usage`` fields.
        ``messages``
            List of all parsed message dicts (role + content).
        ``tool_calls``
            List of ``{"tool_use_id", "tool_name", "tool_input"}`` dicts.
        ``tool_results``
            List of ``{"tool_use_id", "content", "is_error"}`` dicts.
        ``final_response``
            The text content of the *last* assistant message.

    Raises
    ------
    FileNotFoundError
        If *session_dir* does not exist.
    ValueError
        If no JSONL files are found in *session_dir*.
    """
    session_path = Path(session_dir)
    if not session_path.exists():
        raise FileNotFoundError(f"Session directory not found: {session_dir}")

    jsonl_files = sorted(session_path.glob("*.jsonl"))
    if not jsonl_files:
        raise ValueError(f"No JSONL files found in {session_dir}")

    # Initialise accumulators
    messages: list[dict] = []
    tool_calls: list[dict] = []
    tool_results: list[dict] = []
    final_response: str = ""

    # Metadata fields
    session_key: str = session_path.name
    agent_id: str = "unknown"
    model: str = "unknown"
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    for jsonl_file in jsonl_files:
        logger.debug("Parsing JSONL file: %s", jsonl_file)
        with jsonl_file.open("r", encoding="utf-8") as fh:
            for lineno, raw_line in enumerate(fh, start=1):
                raw_line = raw_line.strip()
                if not raw_line:
                    continue

                try:
                    obj = json.loads(raw_line)
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "Skipping malformed JSON at %s:%d — %s",
                        jsonl_file.name,
                        lineno,
                        exc,
                    )
                    continue

                obj_type = obj.get("type", "")

                # ------------------------------------------------------------------
                # Metadata / usage lines
                # ------------------------------------------------------------------
                if obj_type == "metadata" or "session_key" in obj:
                    session_key = obj.get("session_key", session_key)
                    agent_id = obj.get("agent_id", agent_id)
                    model = obj.get("model", model)
                    usage = obj.get("usage", {})
                    total_tokens += usage.get("total_tokens", 0)
                    input_tokens += usage.get("input_tokens", 0)
                    output_tokens += usage.get("output_tokens", 0)
                    continue

                role = obj.get("role", "")
                content = obj.get("content", "")

                # Accumulate usage from any line that has it
                usage = obj.get("usage", {})
                if usage:
                    total_tokens += usage.get("total_tokens", 0)
                    input_tokens += usage.get("input_tokens", 0)
                    output_tokens += usage.get("output_tokens", 0)

                # Accumulate model name
                if obj.get("model"):
                    model = obj["model"]

                if not role:
                    continue  # unrecognised line shape

                # ------------------------------------------------------------------
                # Store the raw message
                # ------------------------------------------------------------------
                messages.append({"role": role, "content": content})

                # ------------------------------------------------------------------
                # Extract tool calls and tool results from content blocks
                # ------------------------------------------------------------------
                content_blocks: list[dict] = []
                if isinstance(content, list):
                    content_blocks = content
                elif isinstance(content, str):
                    # Plain-text message — nothing more to extract
                    if role == "assistant":
                        final_response = content
                    continue

                for block in content_blocks:
                    if not isinstance(block, dict):
                        continue

                    block_type = block.get("type", "")

                    if block_type == "tool_use":
                        tool_calls.append(
                            {
                                "tool_use_id": block.get("id", ""),
                                "tool_name": block.get("name", ""),
                                "tool_input": block.get("input", {}),
                            }
                        )

                    elif block_type == "tool_result":
                        result_content = block.get("content", "")
                        # content may be a list of text blocks
                        if isinstance(result_content, list):
                            result_content = " ".join(
                                b.get("text", "") if isinstance(b, dict) else str(b)
                                for b in result_content
                            )
                        tool_results.append(
                            {
                                "tool_use_id": block.get("tool_use_id", ""),
                                "content": result_content,
                                "is_error": block.get("is_error", False),
                            }
                        )

                    elif block_type == "text" and role == "assistant":
                        # Track the latest assistant text block as final_response
                        final_response = block.get("text", final_response)

    logger.info(
        "Extracted session '%s': %d messages, %d tool calls, %d tool results",
        session_key,
        len(messages),
        len(tool_calls),
        len(tool_results),
    )

    return {
        "session_key": session_key,
        "agent_id": agent_id,
        "model": model,
        "total_tokens": total_tokens,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "messages": messages,
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "final_response": final_response,
    }


# ---------------------------------------------------------------------------
# HuggingFace upload helpers
# ---------------------------------------------------------------------------

def _get_hf_token(hf_token: Optional[str]) -> str:
    """Resolve the HuggingFace API token.

    Priority: explicit argument → ``HF_TOKEN`` env var.

    Raises
    ------
    EnvironmentError
        If no token can be found.
    """
    token = hf_token or os.environ.get("HF_TOKEN")
    if not token:
        raise EnvironmentError(
            "No HuggingFace token supplied.  Either pass `hf_token=` or set "
            "the HF_TOKEN environment variable."
        )
    return token


def upload_session_to_hf(
    session_dir: str,
    dataset_name: str = "AlexChen31337/openclaw-rsi-training",
    hf_token: Optional[str] = None,
) -> str:
    """Extract, scrub, and upload a single session to HuggingFace Hub.

    Parameters
    ----------
    session_dir:
        Path to the session directory containing JSONL file(s).
    dataset_name:
        HuggingFace dataset repository in the format ``owner/repo``.
    hf_token:
        HuggingFace API token.  Falls back to the ``HF_TOKEN`` env var.

    Returns
    -------
    str
        The URL of the uploaded dataset on HuggingFace Hub.

    Raises
    ------
    EnvironmentError
        If no HuggingFace token is available.
    FileNotFoundError / ValueError
        Propagated from :func:`extract_session_data`.
    ImportError
        If the ``datasets`` package is not installed.
    """
    try:
        from datasets import Dataset  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "The 'datasets' package is required.  Install it with: "
            "pip install datasets"
        ) from exc

    token = _get_hf_token(hf_token)

    logger.info("Extracting session data from: %s", session_dir)
    raw_data = extract_session_data(session_dir)

    # Scrub all string fields recursively
    scrubbed = _scrub_value(raw_data)

    # Build a flat record suitable for a HuggingFace Dataset row
    record = {
        "session_key": scrubbed["session_key"],
        "agent_id": scrubbed["agent_id"],
        "model": scrubbed["model"],
        "total_tokens": scrubbed["total_tokens"],
        "input_tokens": scrubbed["input_tokens"],
        "output_tokens": scrubbed["output_tokens"],
        # Serialise complex fields to JSON strings for compatibility
        "messages": json.dumps(scrubbed["messages"]),
        "tool_calls": json.dumps(scrubbed["tool_calls"]),
        "tool_results": json.dumps(scrubbed["tool_results"]),
        "final_response": scrubbed["final_response"],
    }

    dataset = Dataset.from_list([record])

    logger.info("Pushing dataset to HuggingFace Hub: %s", dataset_name)
    dataset.push_to_hub(
        dataset_name,
        token=token,
        private=False,
    )

    url = f"https://huggingface.co/datasets/{dataset_name}"
    logger.info("Dataset uploaded successfully: %s", url)
    return url


def upload_sessions_batch(
    session_dirs: list[str],
    dataset_name: str = "AlexChen31337/openclaw-rsi-training",
    hf_token: Optional[str] = None,
) -> list[str]:
    """Extract, scrub, and batch-upload multiple sessions to HuggingFace Hub.

    All sessions are combined into a single dataset push rather than one push
    per session, which is more efficient for large numbers of sessions.

    Parameters
    ----------
    session_dirs:
        List of paths to session directories.
    dataset_name:
        HuggingFace dataset repository in the format ``owner/repo``.
    hf_token:
        HuggingFace API token.  Falls back to the ``HF_TOKEN`` env var.

    Returns
    -------
    list[str]
        URLs of the uploaded datasets (one per successful session; failures
        are logged and skipped).

    Raises
    ------
    EnvironmentError
        If no HuggingFace token is available.
    ImportError
        If the ``datasets`` package is not installed.
    """
    try:
        from datasets import Dataset  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "The 'datasets' package is required.  Install it with: "
            "pip install datasets"
        ) from exc

    token = _get_hf_token(hf_token)

    records: list[dict] = []
    urls: list[str] = []

    for session_dir in session_dirs:
        try:
            logger.info("Processing session: %s", session_dir)
            raw_data = extract_session_data(session_dir)
            scrubbed = _scrub_value(raw_data)

            record = {
                "session_key": scrubbed["session_key"],
                "agent_id": scrubbed["agent_id"],
                "model": scrubbed["model"],
                "total_tokens": scrubbed["total_tokens"],
                "input_tokens": scrubbed["input_tokens"],
                "output_tokens": scrubbed["output_tokens"],
                "messages": json.dumps(scrubbed["messages"]),
                "tool_calls": json.dumps(scrubbed["tool_calls"]),
                "tool_results": json.dumps(scrubbed["tool_results"]),
                "final_response": scrubbed["final_response"],
            }
            records.append(record)

        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Failed to process session %s: %s", session_dir, exc)

    if not records:
        logger.warning("No valid sessions to upload.")
        return urls

    dataset = Dataset.from_list(records)
    logger.info(
        "Pushing %d session(s) to HuggingFace Hub: %s", len(records), dataset_name
    )
    dataset.push_to_hub(
        dataset_name,
        token=token,
        private=False,
    )

    url = f"https://huggingface.co/datasets/{dataset_name}"
    logger.info("Batch upload successful: %s", url)
    return [url]
