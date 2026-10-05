"""CR-A drošības testi: uzbrukumi atsaukšanai pēc OWASP Top 10 (2021).

Katrs tests mēģina "uzlauzt" POST /submissions/{id}/withdraw un pārbauda,
ka serveris atbild ar paredzētu kļūdu, nevis 500, un ka dati nemainās.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import storage
from app.main import UI_DIR, app

REASON = "Problēma jau ir atrisināta"


@pytest.fixture
def submission_id(client, valid_payload):
    return client.post("/submissions", json=valid_payload).json()["id"]


def statuses(client):
    return {s["id"]: s["status"] for s in client.get("/submissions").json()}


# A03 Injection: SQL injekcija iesnieguma ID


@pytest.mark.parametrize(
    "malicious_id",
    [
        "' OR '1'='1",
        "' OR 1=1 --",
        "IES-2026-000001' OR '1'='1",
        "x'; DROP TABLE submissions; --",
        "x'; UPDATE submissions SET status='WITHDRAWN'; --",
    ],
)
def test_a03_sql_injection_in_id_returns_404(client, submission_id, malicious_id):
    before = statuses(client)

    response = client.post(
        f"/submissions/{malicious_id}/withdraw", json={"reason": REASON}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    # Neviens iesniegums nav mainīts, tabula joprojām eksistē.
    assert statuses(client) == before


def test_a03_sql_injection_in_reason_is_stored_as_text(client, submission_id):
    reason = "x'); DELETE FROM audit; DROP TABLE submissions; --"

    response = client.post(
        f"/submissions/{submission_id}/withdraw", json={"reason": reason}
    )

    assert response.status_code == 200
    audit = client.get(f"/submissions/{submission_id}/audit").json()
    assert [e["action"] for e in audit] == ["CREATE", "WITHDRAW"]
    assert audit[-1]["detail"] == reason
    assert client.get("/submissions").status_code == 200


# A03 Injection: XSS caur iemeslu


def test_a03_xss_reason_returned_only_as_json(client, submission_id):
    reason = "<script>alert('xss')</script><img src=x onerror=alert(1)>"

    client.post(f"/submissions/{submission_id}/withdraw", json={"reason": reason})
    response = client.get(f"/submissions/{submission_id}/audit")

    # JSON, nevis HTML: pārlūks to neizpilda. Teksts saglabāts nemainīts.
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()[-1]["detail"] == reason


@pytest.mark.parametrize(
    "path, body",
    [
        ("/submissions/<script>alert(1)</script>/withdraw", {"reason": REASON}),
        ("/submissions/{id}/withdraw", {"reason": "<script>"}),
        ("/submissions/{id}/withdraw", {"reason": ["<script>alert(1)</script>"]}),
    ],
)
def test_a03_error_does_not_reflect_input(client, submission_id, path, body):
    response = client.post(path.format(id=submission_id), json=body)

    assert response.status_code in (400, 404)
    assert "<script>" not in response.text
    assert "alert" not in response.text


def test_ui_withdraw_shows_api_text_without_html():
    # Forma kļūdas tekstu ieliek ar textContent, ne innerHTML.
    source = (UI_DIR / "index.html").read_text(encoding="utf-8")
    assert "withdrawMessage.textContent = withdrawErrorText" in source
    assert "withdrawMessage.innerHTML = withdrawErrorText" not in source


# A04 Insecure Design / A05 Security Misconfiguration: bojāta ievade


@pytest.mark.parametrize(
    "raw",
    [b"not json", b"{", b'{"reason": ', b"\xff\xfe\x00", b""],
)
def test_a04_malformed_body_returns_400_not_500(client, submission_id, raw):
    response = client.post(
        f"/submissions/{submission_id}/withdraw",
        content=raw,
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert client.get(f"/submissions/{submission_id}").json()["status"] == "RECEIVED"


@pytest.mark.parametrize(
    "reason",
    [12345678901, 1.5, True, ["Problēma jau ir atrisināta"], {"x": REASON}],
)
def test_a04_wrong_reason_type_returns_400(client, submission_id, reason):
    response = client.post(
        f"/submissions/{submission_id}/withdraw", json={"reason": reason}
    )

    assert response.status_code == 400
    assert response.json()["error"]["details"] == [
        {"field": "reason", "issue": "INVALID_FORMAT"}
    ]


@pytest.mark.parametrize("body", [[{"reason": REASON}], "Problēma jau ir atrisināta"])
def test_a04_body_not_object_returns_400(client, submission_id, body):
    response = client.post(f"/submissions/{submission_id}/withdraw", json=body)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_a04_huge_reason_rejected(client, submission_id):
    response = client.post(
        f"/submissions/{submission_id}/withdraw", json={"reason": "a" * 1_000_000}
    )

    assert response.status_code == 400
    assert response.json()["error"]["details"] == [
        {"field": "reason", "issue": "TOO_LONG"}
    ]
    assert client.get(f"/submissions/{submission_id}").json()["status"] == "RECEIVED"


def test_a04_wrong_http_method_does_not_withdraw(client, submission_id):
    for method in ("GET", "PUT", "DELETE", "PATCH"):
        response = client.request(method, f"/submissions/{submission_id}/withdraw")
        assert response.status_code == 405
    assert client.get(f"/submissions/{submission_id}").json()["status"] == "RECEIVED"


@pytest.mark.parametrize(
    "malicious_id", ["..", "../..", "..%2F..%2Fhealth", "%00", "IES-2026-000001%00"]
)
def test_a01_path_tricks_do_not_withdraw(client, submission_id, malicious_id):
    before = statuses(client)

    response = client.post(
        f"/submissions/{malicious_id}/withdraw", json={"reason": REASON}
    )

    assert response.status_code in (404, 405)
    assert statuses(client) == before


# A08 Software and Data Integrity Failures: masu piešķiršana (mass assignment)


def test_a08_extra_fields_cannot_change_other_data(client, submission_id):
    before = client.get(f"/submissions/{submission_id}").json()

    response = client.post(
        f"/submissions/{submission_id}/withdraw",
        json={
            "reason": REASON,
            "status": "ANSWERED",
            "id": "IES-2026-000001",
            "dueDate": "2099-01-01",
            "personalCode": "32000000999",
            "fullName": "Uzbrucējs",
        },
    )

    after = response.json()
    assert response.status_code == 200
    assert after["status"] == "WITHDRAWN"
    for field in ("id", "dueDate", "personalCode", "fullName"):
        assert after[field] == before[field]


# A04: sacensības (race): divas atsaukšanas, tikai viena izdodas


def test_a04_concurrent_withdraw_only_one_succeeds(client, submission_id):
    from concurrent.futures import ThreadPoolExecutor

    def withdraw(_):
        return client.post(
            f"/submissions/{submission_id}/withdraw", json={"reason": REASON}
        ).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(withdraw, range(8)))

    assert sorted(codes) == [200] + [409] * 7
    audit = client.get(f"/submissions/{submission_id}/audit").json()
    assert [e["action"] for e in audit].count("WITHDRAW") == 1


# A05 Security Misconfiguration: neparedzēta kļūda nedrīkst atklāt iekšējo informāciju


def test_a05_unexpected_error_hides_internal_details(
    client, submission_id, monkeypatch
):
    def broken(*args, **kwargs):
        raise sqlite3.OperationalError(
            "no such table: submissions (db=/srv/ezermala/prod.db, user=admin)"
        )

    monkeypatch.setattr(storage, "transition", broken)
    unsafe_client = TestClient(app, raise_server_exceptions=False)

    response = unsafe_client.post(
        f"/submissions/{submission_id}/withdraw", json={"reason": REASON}
    )

    assert response.status_code == 500
    assert "submissions" not in response.text
    assert "prod.db" not in response.text
    assert "admin" not in response.text
