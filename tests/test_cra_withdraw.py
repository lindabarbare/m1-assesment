"""CR-A: iedzīvotājs atsauc iesniegumu (POST /submissions/{id}/withdraw)."""

import logging

import pytest

from app import storage

REASON = "Problēma jau ir atrisināta"


@pytest.fixture
def make_submission(client, valid_payload):
    def _make(status: str = "RECEIVED") -> str:
        submission_id = client.post("/submissions", json=valid_payload).json()["id"]
        if status != "RECEIVED":
            storage.update_status(submission_id, status)
        return submission_id

    return _make


def withdraw(client, submission_id, reason=REASON):
    return client.post(
        f"/submissions/{submission_id}/withdraw", json={"reason": reason}
    )


# 1., 2. kritērijs
@pytest.mark.parametrize("status", ["RECEIVED", "IN_PROGRESS"])
def test_withdraw_allowed_status(client, make_submission, status):
    submission_id = make_submission(status)
    before = client.get(f"/submissions/{submission_id}").json()

    response = withdraw(client, submission_id)

    assert response.status_code == 200
    assert response.json()["status"] == "WITHDRAWN"
    # Precizējums: dueDate nemainās.
    assert response.json()["dueDate"] == before["dueDate"]
    assert client.get(f"/submissions/{submission_id}").json()["status"] == "WITHDRAWN"


# 3., 4. kritērijs. FORWARDED: PĪ lēmums atvērts, pagaidām 409.
@pytest.mark.parametrize("status", ["ANSWERED", "WITHDRAWN", "FORWARDED"])
def test_withdraw_not_allowed_status(client, make_submission, status):
    submission_id = make_submission(status)

    response = withdraw(client, submission_id)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_STATE"
    assert client.get(f"/submissions/{submission_id}").json()["status"] == status
    actions = [
        e["action"] for e in client.get(f"/submissions/{submission_id}/audit").json()
    ]
    assert "WITHDRAW" not in actions


def test_withdraw_twice_returns_409(client, make_submission):
    submission_id = make_submission()
    assert withdraw(client, submission_id).status_code == 200

    response = withdraw(client, submission_id)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_STATE"


# 5. kritērijs
def test_withdraw_unknown_id_returns_404(client):
    response = withdraw(client, "IES-2026-999999")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


# 6. kritērijs
@pytest.mark.parametrize(
    "body, issue",
    [
        ({}, "REQUIRED"),
        ({"reason": None}, "INVALID_FORMAT"),
        ({"reason": "Par īsu"}, "INVALID_FORMAT"),
        ({"reason": "a" * 9}, "INVALID_FORMAT"),
        ({"reason": " " * 20}, "INVALID_FORMAT"),
        ({"reason": "  " + "a" * 9 + "  "}, "INVALID_FORMAT"),
        ({"reason": "a" * 501}, "TOO_LONG"),
    ],
)
def test_withdraw_invalid_reason(client, make_submission, body, issue):
    submission_id = make_submission()

    response = client.post(f"/submissions/{submission_id}/withdraw", json=body)

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"] == [{"field": "reason", "issue": issue}]
    assert client.get(f"/submissions/{submission_id}").json()["status"] == "RECEIVED"


@pytest.mark.parametrize("reason", ["a" * 10, "a" * 500])
def test_withdraw_reason_length_boundaries(client, make_submission, reason):
    submission_id = make_submission()
    assert withdraw(client, submission_id, reason).status_code == 200


# 7. kritērijs
def test_withdraw_writes_audit_entry(client, make_submission):
    submission_id = make_submission()

    withdraw(client, submission_id, f"  {REASON}  ")
    audit = client.get(f"/submissions/{submission_id}/audit").json()

    assert [e["action"] for e in audit] == ["CREATE", "WITHDRAW"]
    assert audit[-1]["detail"] == REASON


# 8. kritērijs
def test_withdraw_does_not_leak_personal_data(
    client, make_submission, valid_payload, caplog
):
    caplog.set_level(logging.DEBUG)
    submission_id = make_submission()
    caplog.clear()

    responses = [
        withdraw(client, submission_id),
        withdraw(client, submission_id),
        withdraw(client, "IES-2026-999999"),
        client.post(f"/submissions/{submission_id}/withdraw", json={"reason": "x"}),
    ]

    secrets = [
        valid_payload["personalCode"],
        valid_payload["fullName"],
        valid_payload["email"],
        valid_payload["body"],
    ]
    for secret in secrets:
        assert secret not in caplog.text
        for response in responses[1:]:
            assert secret not in response.text


def test_withdraw_rolls_back_status_if_audit_fails(client, make_submission):
    submission_id = make_submission()

    def broken_now():
        raise RuntimeError("clock failed")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.clock.now", broken_now)
        with pytest.raises(RuntimeError):
            storage.transition(
                submission_id, ("RECEIVED",), "WITHDRAWN", "WITHDRAW", REASON
            )

    assert client.get(f"/submissions/{submission_id}").json()["status"] == "RECEIVED"
    actions = [
        e["action"] for e in client.get(f"/submissions/{submission_id}/audit").json()
    ]
    assert actions == ["CREATE"]


def test_update_status_log_has_no_personal_data(
    client, make_submission, valid_payload, caplog
):
    submission_id = make_submission()
    caplog.set_level(logging.DEBUG)

    storage.update_status(submission_id, "IN_PROGRESS")

    assert submission_id in caplog.text
    for field in ("personalCode", "fullName", "email", "body"):
        assert valid_payload[field] not in caplog.text
