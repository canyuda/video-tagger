"""Tests for the strict .env.local parser (docs/04 §4: env -> .env.local, fail fast)."""

import pytest

from sidecar.envfile import EnvFileError, parse_env_file, parse_env_text


class TestParseEnvText:
    def test_basic_key_value(self):
        assert parse_env_text("API_KEY=abc123") == {"API_KEY": "abc123"}

    def test_multiple_keys(self):
        text = "A=1\nB=2\n"
        assert parse_env_text(text) == {"A": "1", "B": "2"}

    def test_skips_blank_lines_and_comments(self):
        text = "# comment\n\nA=1\n   # indented comment\nB=2\n"
        assert parse_env_text(text) == {"A": "1", "B": "2"}

    def test_strips_surrounding_double_quotes(self):
        assert parse_env_text('COOKIE="a=b; c=d"') == {"COOKIE": "a=b; c=d"}

    def test_strips_surrounding_single_quotes(self):
        assert parse_env_text("KEY='value'") == {"KEY": "value"}

    def test_keeps_inner_quotes_unbalanced(self):
        # Value with a quote only on one side is taken literally.
        assert parse_env_text("KEY=va\"lue") == {"KEY": 'va"lue'}

    def test_value_may_contain_equals(self):
        assert parse_env_text("COOKIE=a=b; c=d") == {"COOKIE": "a=b; c=d"}

    def test_value_with_spaces_kept(self):
        assert parse_env_text("KEY=hello world ") == {"KEY": "hello world"}

    def test_empty_value_allowed(self):
        assert parse_env_text("EMPTY=") == {"EMPTY": ""}

    def test_last_assignment_wins(self):
        assert parse_env_text("A=1\nA=2") == {"A": "2"}

    def test_export_prefix_rejected(self):
        # Strict parser: no shell syntax. Surface it instead of guessing.
        with pytest.raises(EnvFileError, match="line 1"):
            parse_env_text("export A=1")

    def test_line_without_equals_rejected(self):
        with pytest.raises(EnvFileError, match="line 2"):
            parse_env_text("A=1\nTHIS IS NOT AN ASSIGNMENT")

    def test_invalid_key_name_rejected(self):
        with pytest.raises(EnvFileError):
            parse_env_text("1BAD=oops")

    def test_error_includes_line_number(self):
        with pytest.raises(EnvFileError, match="line 3"):
            parse_env_text("A=1\nB=2\nnope")


class TestParseEnvFile:
    def test_reads_real_file(self, tmp_path):
        f = tmp_path / ".env.local"
        f.write_text("A=1\nB='two'\n")
        assert parse_env_file(f) == {"A": "1", "B": "two"}

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(EnvFileError, match="not found"):
            parse_env_file(tmp_path / "nope.env")
