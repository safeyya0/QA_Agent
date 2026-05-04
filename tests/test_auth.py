import pytest
import re
from tools.auth import generate_test_credentials, detect_auth_forms


# ── generate_test_credentials ──────────────────────────────────────────────────

class TestGenerateTestCredentials:

    def test_returns_email_password_username(self):
        creds = generate_test_credentials()
        assert "email" in creds
        assert "password" in creds
        assert "username" in creds

    def test_email_is_valid_format(self):
        creds = generate_test_credentials()
        assert re.match(r"^test_[a-f0-9]+@testmail\.com$", creds["email"])

    def test_password_meets_complexity(self):
        creds = generate_test_credentials()
        pwd = creds["password"]
        assert len(pwd) >= 8
        assert any(c.isupper() for c in pwd)
        assert any(c.isdigit() for c in pwd)

    def test_credentials_are_unique_each_call(self):
        a = generate_test_credentials()
        b = generate_test_credentials()
        assert a["email"] != b["email"]
        assert a["password"] != b["password"]


# ── detect_auth_forms ──────────────────────────────────────────────────────────

class TestDetectAuthForms:

    def _fields(self, types: list[str], names: list[str] | None = None) -> list[dict]:
        names = names or types
        return [{"type": t, "name": n, "id": n} for t, n in zip(types, names)]

    def test_detects_login_from_url(self):
        fields = self._fields(["email", "password"])
        result = detect_auth_forms(fields, "https://example.com/login", "")
        assert result["has_login"] is True

    def test_detects_login_from_signin_url(self):
        fields = self._fields(["email", "password"])
        result = detect_auth_forms(fields, "https://example.com/signin", "")
        assert result["has_login"] is True

    def test_detects_register_from_url(self):
        fields = self._fields(["email", "password", "password"], ["email", "password", "confirm_password"])
        result = detect_auth_forms(fields, "https://example.com/register", "")
        assert result["has_register"] is True

    def test_detects_register_from_confirm_password_field(self):
        fields = self._fields(
            ["email", "password", "password"],
            ["email", "password", "confirm_password"]
        )
        result = detect_auth_forms(fields, "https://example.com/signup", "")
        assert result["has_register"] is True

    def test_detects_login_from_page_text(self):
        fields = self._fields(["email", "password"])
        result = detect_auth_forms(fields, "https://example.com/", "Welcome back, sign in to continue")
        assert result["has_login"] is True

    def test_detects_register_from_page_text(self):
        fields = self._fields(["email", "password"])
        result = detect_auth_forms(fields, "https://example.com/", "Create your account to get started")
        assert result["has_register"] is True

    def test_login_url_preserved_when_login(self):
        url = "https://example.com/login"
        fields = self._fields(["email", "password"])
        result = detect_auth_forms(fields, url, "")
        assert result["login_url"] == url

    def test_result_has_all_expected_keys(self):
        fields = self._fields(["email", "password"])
        result = detect_auth_forms(fields, "https://example.com/login", "")
        for key in ("has_login", "has_register", "login_url", "register_url", "has_username_field", "fields_detected"):
            assert key in result

    def test_no_auth_signals_returns_false(self):
        fields = self._fields(["text", "text"], ["search", "query"])
        result = detect_auth_forms(fields, "https://example.com/search", "Search results")
        assert result["has_login"] is False
        assert result["has_register"] is False

    def test_detects_username_field(self):
        fields = self._fields(["text", "password"], ["username", "password"])
        result = detect_auth_forms(fields, "https://example.com/login", "")
        assert result["has_username_field"] is True

    def test_fields_detected_returned_unchanged(self):
        fields = self._fields(["email", "password"])
        result = detect_auth_forms(fields, "https://example.com/login", "")
        assert result["fields_detected"] == fields
