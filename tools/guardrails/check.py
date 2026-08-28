"""Fail-fast repository guardrails for architectural and privacy invariants."""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_SUFFIXES = {".py", ".c", ".cc", ".cpp", ".h", ".hpp", ".ts", ".tsx", ".sql"}
CONTENT_IDENTIFIER_DENY = re.compile(
    r"(^|_)(key_?code|character|typed_?text|raw_?text|window_?title|"
    r"document_?name|file_?path|url|clipboard|screen_?shot|screen_?content)($|_)",
    re.IGNORECASE,
)
IGNORED_PARTS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    "data",
    "generated",
    "node_modules",
    "tests",
}


@dataclass(frozen=True)
class Finding:
    rule: str
    path: str
    message: str
    line: int | None = None

    def format(self) -> str:
        location = f"{self.path}:{self.line}" if self.line is not None else self.path
        return f"[{self.rule}] {location}: {self.message}"


def _finding(rule: str, path: str, message: str, line: int | None = None) -> list[Finding]:
    return [Finding(rule=rule, path=path, message=message, line=line)]


def _lines_matching(text: str, pattern: re.Pattern[str]) -> Iterable[tuple[int, str]]:
    for line_number, line in enumerate(text.splitlines(), start=1):
        if pattern.search(line):
            yield line_number, line.strip()


def check_schema_identifiers(path: str, document: object) -> list[Finding]:
    """Reject fields capable of carrying content at contract/storage boundaries."""

    findings: list[Finding] = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            properties = value.get("properties")
            if isinstance(properties, dict):
                for identifier in properties:
                    if CONTENT_IDENTIFIER_DENY.search(identifier):
                        findings.extend(
                            _finding(
                                "G01_CONTENT_FIELD",
                                path,
                                f"forbidden content-bearing field name: {identifier}",
                            )
                        )
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(document)
    return findings


def check_sql_schema_identifiers(path: str, text: str) -> list[Finding]:
    """Reject content-bearing SQLite column names in CREATE TABLE migrations."""

    findings: list[Finding] = []
    table_blocks = re.finditer(
        r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+\w+\s*\((.*?)\);",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    constraint_prefixes = {"CHECK", "CONSTRAINT", "FOREIGN", "PRIMARY", "UNIQUE"}
    for block in table_blocks:
        body = block.group(1)
        column_starts = re.finditer(r"(?:^|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s+", body)
        for column_start in column_starts:
            identifier = column_start.group(1)
            if identifier.upper() in constraint_prefixes:
                continue
            if CONTENT_IDENTIFIER_DENY.search(identifier):
                line = text[: block.start(1) + column_start.start(1)].count("\n") + 1
                findings.extend(
                    _finding(
                        "G01_CONTENT_FIELD",
                        path,
                        f"forbidden content-bearing SQL column: {identifier}",
                        line,
                    )
                )
    return findings


def check_collector_privacy(path: str, text: str) -> list[Finding]:
    deny = re.compile(
        r"GetWindowText|UIAutomation|Accessibility|GetClipboard|clipboard|"
        r"screen[_-]?(capture|shot)|OCR|window[_-]?title|document[_-]?name|\bURL\b",
        re.IGNORECASE,
    )
    return [
        Finding("G02_COLLECTOR_PRIVACY", path, f"forbidden collector capability: {line}", number)
        for number, line in _lines_matching(text, deny)
    ]


def check_prohibited_dependency(path: str, text: str) -> list[Finding]:
    deny = re.compile(
        r"\b(opencv|tesseract|pytesseract|pyautogui|uiautomation|"
        r"accessibility[_-]?api|screen[_-]?capture|electron-desktop-capturer)\b",
        re.IGNORECASE,
    )
    return [
        Finding(
            "G03_PROHIBITED_DEPENDENCY", path, f"prohibited dependency/capability: {line}", number
        )
        for number, line in _lines_matching(text, deny)
    ]


def check_temporal_identity_features(path: str, text: str) -> list[Finding]:
    deny = re.compile(
        r"\b(time[_-]?of[_-]?day|day[_-]?of[_-]?week|hour[_-]?of[_-]?day|weekday)\b",
        re.IGNORECASE,
    )
    return [
        Finding(
            "G04_TEMPORAL_IDENTITY", path, f"absolute temporal identity feature: {line}", number
        )
        for number, line in _lines_matching(text, deny)
    ]


def check_app_specific_model(path: str, text: str) -> list[Finding]:
    patterns = (
        re.compile(r"\b(if|switch)\b[^\n]*(app_id|application|category)[^\n]*(model|train)", re.I),
        re.compile(r"(model|train)[^\n]*(by_app|per_app|app_specific)", re.I),
    )
    findings: list[Finding] = []
    for pattern in patterns:
        findings.extend(
            Finding(
                "G05_APP_SPECIFIC_MODEL",
                path,
                f"application-conditional model path: {line}",
                number,
            )
            for number, line in _lines_matching(text, pattern)
        )
    return findings


def check_monitoring_invariants(path: str, text: str) -> list[Finding]:
    patterns = (
        re.compile(r"monitor(ing)?[_-]?(enabled|active)\s*[:=]\s*(false|0)\b", re.I),
        re.compile(r"confidence[_-]?(floor|minimum|min)\s*[:=]\s*0(?:\.0+)?\b", re.I),
        re.compile(r"disable[_-]?monitoring\s*\(", re.I),
    )
    findings: list[Finding] = []
    for pattern in patterns:
        findings.extend(
            Finding(
                "G06_MONITORING_DISABLED",
                path,
                f"monitoring/confidence can be disabled: {line}",
                number,
            )
            for number, line in _lines_matching(text, pattern)
        )
    return findings


def check_training_gate(path: str, text: str) -> list[Finding]:
    training_call = re.search(r"\.(fit|partial_fit)\s*\(|\btrain_model\s*\(", text)
    if training_call and "require_promotion_gate" not in text:
        line = text[: training_call.start()].count("\n") + 1
        return _finding(
            "G07_PROMOTION_GATE",
            path,
            "training call lacks the require_promotion_gate boundary",
            line,
        )
    return []


def check_shared_feature_consumer(path: str, text: str) -> list[Finding]:
    if re.search(r"\b(def|function)\s+(extract|compute|build)_.*feature", text, re.I):
        return _finding(
            "G08_SHARED_FEATURES",
            path,
            "consumer reimplements feature extraction instead of importing ml.features",
        )
    if "ml.features" not in text:
        return _finding(
            "G08_SHARED_FEATURES",
            path,
            "feature consumer must import the single ml.features implementation",
        )
    return []


TUNABLE_NAME = re.compile(
    r"(threshold|weight|window|cadence|cooldown|quarantine|confidence_floor|"
    r"ewma_alpha|breach_[kn]|min_keystrokes|min_mouse_samples|action_budget)",
    re.IGNORECASE,
)


def check_hardcoded_tunables(path: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            target = node.target if isinstance(node, ast.AnnAssign) else node.targets[0]
            value = node.value
            if (
                isinstance(target, ast.Name)
                and TUNABLE_NAME.search(target.id)
                and isinstance(value, ast.Constant)
                and isinstance(value.value, (int, float))
            ):
                findings.extend(
                    _finding(
                        "G09_HARDCODED_TUNABLE",
                        path,
                        f"numeric tunable {target.id} must be loaded from config/",
                        node.lineno,
                    )
                )
    else:
        pattern = re.compile(
            rf"\b[A-Za-z_]*{TUNABLE_NAME.pattern}[A-Za-z_]*\s*[:=]\s*[-+]?\d+(?:\.\d+)?",
            re.IGNORECASE,
        )
        findings.extend(
            Finding(
                "G09_HARDCODED_TUNABLE", path, f"numeric tunable outside config/: {line}", number
            )
            for number, line in _lines_matching(text, pattern)
        )
    return findings


def check_data_tracking(tracked_paths: Iterable[str]) -> list[Finding]:
    return [
        Finding("G10_TRACKED_DATA", path, "files below data/ must never be tracked")
        for path in tracked_paths
        if Path(path).parts and Path(path).parts[0].lower() == "data"
    ]


def check_model_loader(path: str, text: str) -> list[Finding]:
    load_call = re.search(r"\b(load_model|joblib\.load|pickle\.load)\s*\(", text)
    if load_call and "assert_feature_schema_compatible" not in text:
        line = text[: load_call.start()].count("\n") + 1
        return _finding(
            "G11_MODEL_SCHEMA_MISMATCH",
            path,
            "model load lacks assert_feature_schema_compatible",
            line,
        )
    return []


def _production_files() -> Iterable[Path]:
    roots = [ROOT / name for name in ("collector", "backend", "ml", "dashboard")]
    for base in roots:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if (
                path.is_file()
                and path.suffix.lower() in SOURCE_SUFFIXES
                and not any(part in IGNORED_PARTS for part in path.relative_to(ROOT).parts)
            ):
                yield path


def _tracked_data() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--", "data"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def scan_repository() -> list[Finding]:
    findings: list[Finding] = []
    schema_dir = ROOT / "protocol" / "schemas"
    for path in schema_dir.glob("*.json"):
        document = json.loads(path.read_text(encoding="utf-8"))
        findings.extend(check_schema_identifiers(str(path.relative_to(ROOT)), document))

    manifest_names = {"pyproject.toml", "requirements.txt", "package.json", "CMakeLists.txt"}
    for path in ROOT.rglob("*"):
        if path.is_file() and path.name in manifest_names:
            relative = path.relative_to(ROOT)
            if not any(part in IGNORED_PARTS for part in relative.parts):
                findings.extend(
                    check_prohibited_dependency(str(relative), path.read_text(encoding="utf-8"))
                )

    for path in _production_files():
        relative = path.relative_to(ROOT)
        path_text = relative.as_posix()
        text = path.read_text(encoding="utf-8")
        if relative.parts[0] == "collector":
            findings.extend(check_collector_privacy(path_text, text))
        if path.suffix.lower() == ".sql":
            findings.extend(check_sql_schema_identifiers(path_text, text))
        findings.extend(check_prohibited_dependency(path_text, text))
        if relative.parts[0] in {"backend", "ml"}:
            findings.extend(check_temporal_identity_features(path_text, text))
            findings.extend(check_app_specific_model(path_text, text))
            findings.extend(check_monitoring_invariants(path_text, text))
            findings.extend(check_hardcoded_tunables(path_text, text))
        if path_text.startswith("ml/training/") or path_text.startswith("backend/app/updates/"):
            findings.extend(check_training_gate(path_text, text))
        if path_text.startswith("backend/app/features/") and path.name != "__init__.py":
            findings.extend(check_shared_feature_consumer(path_text, text))
        if path_text.startswith("backend/app/models/"):
            findings.extend(check_model_loader(path_text, text))

    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    if not re.search(r"(?m)^data/\s*$", gitignore):
        findings.extend(
            _finding("G10_TRACKED_DATA", ".gitignore", "missing exact data/ ignore rule")
        )
    findings.extend(check_data_tracking(_tracked_data()))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    findings = scan_repository()
    if findings:
        for finding in findings:
            print(finding.format())
        print(f"guardrails failed: {len(findings)} violation(s)")
        return 1
    if not args.quiet:
        print("guardrails passed: G01-G11")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
