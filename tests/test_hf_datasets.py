"""
Tests for rsi_loop/integrations/hf_datasets.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Make sure the package root is on the path regardless of how tests are run.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from rsi_loop.integrations.hf_datasets import (
    extract_session_data,
    scrub_personal_info,
    upload_session_to_hf,
    upload_sessions_batch,
)


# ---------------------------------------------------------------------------
# Helper: build a temporary session directory with given JSONL content
# ---------------------------------------------------------------------------

def _make_session_dir(lines: list[dict]) -> tempfile.TemporaryDirectory:
    """Create a temp directory with a single ``session.jsonl`` file."""
    tmpdir = tempfile.TemporaryDirectory()
    jsonl_path = Path(tmpdir.name) / "session.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for obj in lines:
            fh.write(json.dumps(obj) + "\n")
    return tmpdir


# ===========================================================================
# scrub_personal_info
# ===========================================================================

class TestScrubPersonalInfo(unittest.TestCase):
    """Unit tests for scrub_personal_info."""

    # --- Phone numbers ---

    def test_removes_au_phone_no_space(self):
        result = scrub_personal_info("Call me at +61412345678 tomorrow.")
        self.assertNotIn("+61412345678", result)
        self.assertIn("[PHONE_REDACTED]", result)

    def test_removes_au_phone_with_space(self):
        result = scrub_personal_info("Mobile: +61 4 1234 5678")
        self.assertNotIn("61 4 1234 5678", result)

    # --- Email addresses ---

    def test_removes_generic_email(self):
        result = scrub_personal_info("Send to user@example.com please.")
        self.assertNotIn("user@example.com", result)
        self.assertIn("[EMAIL_REDACTED]", result)

    def test_removes_bowen_email(self):
        result = scrub_personal_info("bowen31337@outlook.com is the contact.")
        self.assertNotIn("bowen31337@outlook.com", result)

    def test_removes_alex_email(self):
        result = scrub_personal_info("also: alex.chen31337@gmail.com")
        self.assertNotIn("alex.chen31337@gmail.com", result)

    # --- API keys ---

    def test_removes_sk_ant_key(self):
        secret = "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890abcdefghij"
        result = scrub_personal_info(f"token={secret}")
        self.assertNotIn(secret, result)
        self.assertIn("[API_KEY_REDACTED]", result)

    def test_removes_gh_token(self):
        result = scrub_personal_info("export GH_TOKEN=ghp_ABCDEF1234567890abcdef")
        self.assertNotIn("ghp_ABCDEF1234567890abcdef", result)
        self.assertIn("[TOKEN_REDACTED]", result)

    def test_removes_api_key_assignment_equals(self):
        result = scrub_personal_info('api_key = "supersecret123"')
        self.assertNotIn("supersecret123", result)
        self.assertIn("[API_KEY_REDACTED]", result)

    def test_removes_api_key_assignment_colon(self):
        result = scrub_personal_info("api_key: mysecrettoken")
        self.assertNotIn("mysecrettoken", result)

    # --- Encrypted paths ---

    def test_removes_enc_path(self):
        result = scrub_personal_info(
            'load("memory/encrypted/session_data.enc")'
        )
        self.assertNotIn("memory/encrypted/session_data.enc", result)
        self.assertIn("[ENC_PATH_REDACTED]", result)

    def test_removes_enc_path_no_extension(self):
        result = scrub_personal_info("memory/encrypted/somefile")
        self.assertNotIn("memory/encrypted/somefile", result)

    # --- Hex tokens ---

    def test_removes_long_hex_token(self):
        hex_token = "a" * 32  # exactly 32 hex chars
        result = scrub_personal_info(f"token={hex_token}")
        self.assertNotIn(hex_token, result)
        self.assertIn("[HEX_TOKEN_REDACTED]", result)

    def test_removes_64_char_hex_token(self):
        hex_token = "deadbeef" * 8  # 64 hex chars
        result = scrub_personal_info(hex_token)
        self.assertNotIn(hex_token, result)

    # --- IP addresses ---

    def test_removes_10_0_0_x(self):
        result = scrub_personal_info("Server at 10.0.0.5 is down.")
        self.assertNotIn("10.0.0.5", result)
        self.assertIn("[IP_REDACTED]", result)

    def test_removes_135_181_157_x(self):
        result = scrub_personal_info("Host: 135.181.157.42")
        self.assertNotIn("135.181.157.42", result)
        self.assertIn("[IP_REDACTED]", result)

    # --- Wallet addresses ---

    def test_removes_wallet_address(self):
        wallet = "RD" + "A" * 25
        result = scrub_personal_info(f"Send to {wallet}.")
        self.assertNotIn(wallet, result)
        self.assertIn("[WALLET_REDACTED]", result)

    # --- Passwords ---

    def test_removes_password_equals(self):
        result = scrub_personal_info('password = "hunter2"')
        self.assertNotIn("hunter2", result)
        self.assertIn("[PASSWORD_REDACTED]", result)

    def test_removes_password_colon(self):
        result = scrub_personal_info("password: s3cr3t")
        self.assertNotIn("s3cr3t", result)

    # --- Preservation tests ---

    def test_preserves_tool_names(self):
        text = "Called tool read_file with path /home/user/project/main.py"
        result = scrub_personal_info(text)
        self.assertIn("read_file", result)
        self.assertIn("/home/user/project/main.py", result)

    def test_preserves_short_hex(self):
        """Hex strings shorter than 32 chars should NOT be redacted."""
        short_hex = "deadbeef"  # 8 chars — a normal debug value
        result = scrub_personal_info(f"crc={short_hex}")
        self.assertIn(short_hex, result)

    def test_preserves_normal_ip(self):
        """IPs outside the private/server ranges should be kept."""
        result = scrub_personal_info("Google DNS is at 8.8.8.8")
        self.assertIn("8.8.8.8", result)

    def test_preserves_non_sensitive_text(self):
        text = "The agent completed the task successfully with 42 steps."
        self.assertEqual(scrub_personal_info(text), text)

    def test_non_string_passthrough(self):
        """Non-string values are returned unchanged."""
        self.assertEqual(scrub_personal_info(123), 123)  # type: ignore[arg-type]
        self.assertIsNone(scrub_personal_info(None))    # type: ignore[arg-type]


# ===========================================================================
# extract_session_data
# ===========================================================================

class TestExtractSessionData(unittest.TestCase):
    """Unit tests for extract_session_data."""

    def _sample_lines(self) -> list[dict]:
        return [
            # Metadata line
            {
                "type": "metadata",
                "session_key": "sess_abc123",
                "agent_id": "agent_42",
                "model": "claude-3-5-sonnet-20241022",
                "usage": {"total_tokens": 100, "input_tokens": 80, "output_tokens": 20},
            },
            # User message (plain text)
            {"role": "user", "content": "Please list files in /tmp"},
            # Assistant message with tool_use block
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I will list the files for you."},
                    {
                        "type": "tool_use",
                        "id": "toolu_001",
                        "name": "bash",
                        "input": {"command": "ls /tmp"},
                    },
                ],
            },
            # User message with tool_result
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_001",
                        "content": "file1.txt\nfile2.txt",
                        "is_error": False,
                    }
                ],
            },
            # Final assistant response
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "The directory contains file1.txt and file2.txt."},
                ],
            },
        ]

    def test_basic_extraction(self):
        with _make_session_dir(self._sample_lines()) as tmpdir:
            result = extract_session_data(tmpdir)

        self.assertEqual(result["session_key"], "sess_abc123")
        self.assertEqual(result["agent_id"], "agent_42")
        self.assertEqual(result["model"], "claude-3-5-sonnet-20241022")
        self.assertEqual(result["total_tokens"], 100)
        self.assertEqual(result["input_tokens"], 80)
        self.assertEqual(result["output_tokens"], 20)

    def test_tool_calls_extracted(self):
        with _make_session_dir(self._sample_lines()) as tmpdir:
            result = extract_session_data(tmpdir)

        self.assertEqual(len(result["tool_calls"]), 1)
        tc = result["tool_calls"][0]
        self.assertEqual(tc["tool_use_id"], "toolu_001")
        self.assertEqual(tc["tool_name"], "bash")
        self.assertEqual(tc["tool_input"], {"command": "ls /tmp"})

    def test_tool_results_extracted(self):
        with _make_session_dir(self._sample_lines()) as tmpdir:
            result = extract_session_data(tmpdir)

        self.assertEqual(len(result["tool_results"]), 1)
        tr = result["tool_results"][0]
        self.assertEqual(tr["tool_use_id"], "toolu_001")
        self.assertIn("file1.txt", tr["content"])
        self.assertFalse(tr["is_error"])

    def test_final_response(self):
        with _make_session_dir(self._sample_lines()) as tmpdir:
            result = extract_session_data(tmpdir)

        self.assertIn("file1.txt", result["final_response"])
        self.assertIn("file2.txt", result["final_response"])

    def test_messages_count(self):
        with _make_session_dir(self._sample_lines()) as tmpdir:
            result = extract_session_data(tmpdir)

        # metadata line is skipped; 4 role-bearing lines remain
        self.assertEqual(len(result["messages"]), 4)

    def test_plain_text_final_response(self):
        """Plain string content assistant message sets final_response."""
        lines = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        with _make_session_dir(lines) as tmpdir:
            result = extract_session_data(tmpdir)

        self.assertEqual(result["final_response"], "Hi there!")

    def test_missing_directory_raises(self):
        with self.assertRaises(FileNotFoundError):
            extract_session_data("/nonexistent/path/that/does/not/exist")

    def test_empty_directory_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError):
                extract_session_data(tmpdir)

    def test_skips_malformed_json_lines(self):
        """Malformed lines are skipped without raising."""
        with tempfile.TemporaryDirectory() as tmpdir:
            jsonl_path = Path(tmpdir) / "session.jsonl"
            with jsonl_path.open("w") as fh:
                fh.write("not json at all\n")
                fh.write(json.dumps({"role": "user", "content": "Hello"}) + "\n")

            result = extract_session_data(tmpdir)

        self.assertEqual(len(result["messages"]), 1)

    def test_default_values_when_no_metadata(self):
        """When no metadata line is present, defaults are used."""
        lines = [{"role": "user", "content": "Hello"}]
        with _make_session_dir(lines) as tmpdir:
            result = extract_session_data(tmpdir)

        self.assertEqual(result["agent_id"], "unknown")
        self.assertEqual(result["model"], "unknown")
        self.assertEqual(result["total_tokens"], 0)

    def test_token_accumulation_across_messages(self):
        """Usage in individual message lines is accumulated."""
        lines = [
            {
                "role": "assistant",
                "content": "Turn 1",
                "usage": {"total_tokens": 50, "input_tokens": 30, "output_tokens": 20},
            },
            {
                "role": "assistant",
                "content": "Turn 2",
                "usage": {"total_tokens": 60, "input_tokens": 40, "output_tokens": 20},
            },
        ]
        with _make_session_dir(lines) as tmpdir:
            result = extract_session_data(tmpdir)

        self.assertEqual(result["total_tokens"], 110)
        self.assertEqual(result["input_tokens"], 70)
        self.assertEqual(result["output_tokens"], 40)

    def test_tool_result_list_content(self):
        """tool_result content that is a list of text blocks is joined."""
        lines = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_xyz",
                        "content": [
                            {"type": "text", "text": "Part A"},
                            {"type": "text", "text": "Part B"},
                        ],
                        "is_error": False,
                    }
                ],
            }
        ]
        with _make_session_dir(lines) as tmpdir:
            result = extract_session_data(tmpdir)

        self.assertEqual(len(result["tool_results"]), 1)
        self.assertIn("Part A", result["tool_results"][0]["content"])
        self.assertIn("Part B", result["tool_results"][0]["content"])


# ===========================================================================
# upload_session_to_hf
# ===========================================================================

class TestUploadSessionToHf(unittest.TestCase):
    """Tests for upload_session_to_hf using mock Dataset."""

    def _sample_lines(self) -> list[dict]:
        return [
            {
                "type": "metadata",
                "session_key": "sess_upload_test",
                "agent_id": "agent_upload",
                "model": "claude-opus-4",
            },
            {"role": "user", "content": "Do something"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Done! Contact admin@corp.com if issues."},
                ],
            },
        ]

    @patch("datasets.Dataset")
    def test_returns_hf_url(self, MockDataset):
        mock_ds_instance = MagicMock()
        MockDataset.from_list.return_value = mock_ds_instance

        with _make_session_dir(self._sample_lines()) as tmpdir:
            url = upload_session_to_hf(
                tmpdir,
                dataset_name="TestOwner/test-dataset",
                hf_token="hf_testtoken123",
            )

        self.assertEqual(url, "https://huggingface.co/datasets/TestOwner/test-dataset")

    @patch("datasets.Dataset")
    def test_push_to_hub_called(self, MockDataset):
        mock_ds_instance = MagicMock()
        MockDataset.from_list.return_value = mock_ds_instance

        with _make_session_dir(self._sample_lines()) as tmpdir:
            upload_session_to_hf(
                tmpdir,
                dataset_name="TestOwner/test-dataset",
                hf_token="hf_testtoken123",
            )

        mock_ds_instance.push_to_hub.assert_called_once_with(
            "TestOwner/test-dataset",
            token="hf_testtoken123",
            private=False,
        )

    @patch("datasets.Dataset")
    def test_personal_info_scrubbed_before_upload(self, MockDataset):
        """Email in assistant content must be scrubbed before upload."""
        mock_ds_instance = MagicMock()
        MockDataset.from_list.return_value = mock_ds_instance

        with _make_session_dir(self._sample_lines()) as tmpdir:
            upload_session_to_hf(
                tmpdir,
                dataset_name="TestOwner/test-dataset",
                hf_token="hf_testtoken123",
            )

        call_args = MockDataset.from_list.call_args
        records = call_args[0][0]  # first positional arg is the list
        self.assertEqual(len(records), 1)
        record = records[0]
        # The email admin@corp.com should have been scrubbed
        self.assertNotIn("admin@corp.com", record.get("messages", ""))
        self.assertNotIn("admin@corp.com", record.get("final_response", ""))

    def test_raises_without_token(self):
        """EnvironmentError raised if no token is available."""
        with _make_session_dir(self._sample_lines()) as tmpdir:
            with patch.dict(os.environ, {}, clear=True):
                # Remove HF_TOKEN if set
                os.environ.pop("HF_TOKEN", None)
                with self.assertRaises(EnvironmentError):
                    upload_session_to_hf(tmpdir, hf_token=None)

    @patch("datasets.Dataset")
    def test_uses_hf_token_env_var(self, MockDataset):
        """Token is read from HF_TOKEN env var when not passed explicitly."""
        mock_ds_instance = MagicMock()
        MockDataset.from_list.return_value = mock_ds_instance

        with _make_session_dir(self._sample_lines()) as tmpdir:
            with patch.dict(os.environ, {"HF_TOKEN": "hf_env_token_xyz"}):
                upload_session_to_hf(
                    tmpdir,
                    dataset_name="TestOwner/test-dataset",
                    hf_token=None,
                )

        mock_ds_instance.push_to_hub.assert_called_once()
        _, kwargs = mock_ds_instance.push_to_hub.call_args
        self.assertEqual(kwargs["token"], "hf_env_token_xyz")

    @patch("datasets.Dataset")
    def test_from_list_receives_correct_keys(self, MockDataset):
        """Dataset.from_list receives records with expected keys."""
        mock_ds_instance = MagicMock()
        MockDataset.from_list.return_value = mock_ds_instance

        with _make_session_dir(self._sample_lines()) as tmpdir:
            upload_session_to_hf(
                tmpdir,
                dataset_name="TestOwner/test-dataset",
                hf_token="hf_tok",
            )

        records = MockDataset.from_list.call_args[0][0]
        expected_keys = {
            "session_key", "agent_id", "model",
            "total_tokens", "input_tokens", "output_tokens",
            "messages", "tool_calls", "tool_results", "final_response",
        }
        self.assertEqual(set(records[0].keys()), expected_keys)


# ===========================================================================
# upload_sessions_batch
# ===========================================================================

class TestUploadSessionsBatch(unittest.TestCase):
    """Tests for upload_sessions_batch."""

    def _make_two_sessions(self) -> tuple[tempfile.TemporaryDirectory, tempfile.TemporaryDirectory]:
        lines_a = [
            {"type": "metadata", "session_key": "sess_A", "agent_id": "a1", "model": "m1"},
            {"role": "user", "content": "Hello A"},
            {"role": "assistant", "content": "Reply A"},
        ]
        lines_b = [
            {"type": "metadata", "session_key": "sess_B", "agent_id": "a2", "model": "m2"},
            {"role": "user", "content": "Hello B"},
            {"role": "assistant", "content": "Reply B"},
        ]
        return _make_session_dir(lines_a), _make_session_dir(lines_b)

    @patch("datasets.Dataset")
    def test_batch_combines_records(self, MockDataset):
        mock_ds_instance = MagicMock()
        MockDataset.from_list.return_value = mock_ds_instance

        tmp_a, tmp_b = self._make_two_sessions()
        try:
            urls = upload_sessions_batch(
                [tmp_a.name, tmp_b.name],
                dataset_name="TestOwner/batch-dataset",
                hf_token="hf_tok",
            )
        finally:
            tmp_a.cleanup()
            tmp_b.cleanup()

        records = MockDataset.from_list.call_args[0][0]
        self.assertEqual(len(records), 2)
        session_keys = {r["session_key"] for r in records}
        self.assertEqual(session_keys, {"sess_A", "sess_B"})

    @patch("datasets.Dataset")
    def test_batch_skips_bad_sessions(self, MockDataset):
        """A non-existent directory is skipped; good sessions still upload."""
        mock_ds_instance = MagicMock()
        MockDataset.from_list.return_value = mock_ds_instance

        lines = [
            {"type": "metadata", "session_key": "sess_good"},
            {"role": "user", "content": "ok"},
            {"role": "assistant", "content": "great"},
        ]
        with _make_session_dir(lines) as good_dir:
            urls = upload_sessions_batch(
                [good_dir, "/nonexistent/bad/path"],
                dataset_name="TestOwner/batch-dataset",
                hf_token="hf_tok",
            )

        records = MockDataset.from_list.call_args[0][0]
        self.assertEqual(len(records), 1)

    @patch("datasets.Dataset")
    def test_batch_returns_url_list(self, MockDataset):
        mock_ds_instance = MagicMock()
        MockDataset.from_list.return_value = mock_ds_instance

        tmp_a, tmp_b = self._make_two_sessions()
        try:
            urls = upload_sessions_batch(
                [tmp_a.name, tmp_b.name],
                "test-ds",
                hf_token="hf_tok",
            )
            self.assertEqual(len(urls), 1)
        finally:
            tmp_a.cleanup()
            tmp_b.cleanup()


if __name__ == "__main__":
    unittest.main()
