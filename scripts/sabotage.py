"""CR-A sabotāžas pārbaude: vai testi pamana kļūdu kodā.

Katru reizi sabojā vienu koda vietu, palaiž testus un atjauno failu.
Ja kādu sabotāžu neviens tests nepamana, beidz darbu ar kodu 1.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WITHDRAWABLE = (
    "WITHDRAWABLE = (SubmissionStatus.RECEIVED.value, "
    "SubmissionStatus.IN_PROGRESS.value"
)
LEAK = (
    '    logger.info("Statuss mainīts: %s -> %s %s", submission_id, status, '
    "dict(_conn.execute("
    '"SELECT * FROM submissions WHERE id = ?", (submission_id,)).fetchone()))'
)
LOG_LINE = '    logger.info("Statuss mainīts: %s -> %s", submission_id, status)'

# (apraksts, fails, oriģinālais teksts, sabojātais teksts)
SABOTAGES = [
    (
        "1., 2. kritērijs: IN_PROGRESS nav atļauts",
        "app/main.py",
        WITHDRAWABLE + ")",
        "WITHDRAWABLE = (SubmissionStatus.RECEIVED.value,)",
    ),
    (
        "3. kritērijs: ANSWERED ir atļauts",
        "app/main.py",
        WITHDRAWABLE + ")",
        WITHDRAWABLE + ", SubmissionStatus.ANSWERED.value)",
    ),
    (
        "4. kritērijs: WITHDRAWN ir atļauts",
        "app/main.py",
        WITHDRAWABLE + ")",
        WITHDRAWABLE + ", SubmissionStatus.WITHDRAWN.value)",
    ),
    (
        "5. kritērijs: nav 404 pārbaudes",
        "app/main.py",
        "    if storage.get(submission_id) is None:\n"
        "        raise SubmissionNotFound()\n"
        "    changed = storage.transition(",
        "    changed = storage.transition(",
    ),
    (
        "6. kritērijs: min_length=1",
        "app/models.py",
        "min_length=10, max_length=500",
        "min_length=1, max_length=500",
    ),
    (
        "6. kritērijs: max_length=5000",
        "app/models.py",
        "min_length=10, max_length=500",
        "min_length=10, max_length=5000",
    ),
    (
        "6. kritērijs: atstarpes malās skaita",
        "app/models.py",
        "strip_whitespace=True",
        "strip_whitespace=False",
    ),
    (
        "7. kritērijs: auditā nav iemesla",
        "app/main.py",
        '        "WITHDRAW",\n        request.reason,',
        '        "WITHDRAW",\n        None,',
    ),
    (
        "8. kritērijs: personas dati žurnālā (atsaukšana)",
        "app/storage.py",
        LOG_LINE + "\n    return True",
        LEAK + "\n    return True",
    ),
    (
        "8. kritērijs: personas dati žurnālā (statusa maiņa)",
        "app/storage.py",
        LOG_LINE + "\n    return record",
        LEAK + "\n    return record",
    ),
    (
        "Transakcija: nav ROLLBACK",
        "app/storage.py",
        '            _conn.execute("ROLLBACK TO transition")\n',
        "",
    ),
]


def run_tests() -> list[str]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    failed = re.findall(r"^FAILED \S+::(\w+)", result.stdout, re.MULTILINE)
    return sorted(set(failed))


def main() -> int:
    if run_tests():
        print("Testi krīt jau pirms sabotāžas. Vispirms izlabojiet tos.")
        return 1

    survived = 0
    for description, file, original, broken in SABOTAGES:
        path = ROOT / file
        source = path.read_text(encoding="utf-8")
        if source.count(original) != 1:
            print(f"? {description}: {file} vairs nesakrīt, atjauniniet skriptu")
            survived += 1
            continue
        line = source[: source.index(original)].count("\n") + 1
        try:
            path.write_text(source.replace(original, broken), encoding="utf-8")
            failed = run_tests()
        finally:
            path.write_text(source, encoding="utf-8")

        if failed:
            print(f"✓ {description} ({file}:{line})")
            for name in failed:
                print(f"    sarkans: {name}")
        else:
            survived += 1
            print(f"✗ {description} ({file}:{line}): neviens tests nepamanīja")

    print(f"\nNotverts {len(SABOTAGES) - survived} no {len(SABOTAGES)}.")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
