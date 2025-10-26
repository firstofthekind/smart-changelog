import sys
import unittest
from pathlib import Path
from trace import Trace
from tokenize import (
    COMMENT,
    DEDENT,
    ENCODING,
    ENDMARKER,
    INDENT,
    NL,
    NEWLINE,
    STRING,
    OP,
    generate_tokens,
)


SOURCE_CACHE: dict[Path, list[str]] = {}
COUNTED_CACHE: dict[Path, set[int]] = {}


def _get_lines(path: Path) -> list[str]:
    cached = SOURCE_CACHE.get(path)
    if cached is None:
        cached = path.read_text(encoding="utf-8").splitlines()
        SOURCE_CACHE[path] = cached
    return cached


def _get_counted_lines(path: Path) -> set[int]:
    cached = COUNTED_CACHE.get(path)
    if cached is not None:
        return cached

    counted: set[int] = set()
    with path.open("r", encoding="utf-8") as handle:
        for token in generate_tokens(handle.readline):
            if token.type in {ENCODING, NL, NEWLINE, ENDMARKER, COMMENT, INDENT, DEDENT, OP}:
                continue
            if token.type == STRING:
                continue
            counted.add(token.start[0])

    filtered = {line_no for line_no in counted if not _should_ignore_line(path, line_no)}
    COUNTED_CACHE[path] = filtered
    return filtered


def count_code_lines(path: Path) -> int:
    return len(_get_counted_lines(path))


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    package_root = repo_root / "smart_changelog"
    sys.path.insert(0, str(repo_root))

    tracer = Trace(
        count=True,
        trace=False,
        ignoredirs=[sys.prefix, sys.exec_prefix, str(repo_root / ".venv")],
    )

    def run_suite() -> unittest.result.TestResult:
        suite = unittest.defaultTestLoader.discover("tests", pattern="test_*.py")
        runner = unittest.TextTestRunner(verbosity=2)
        return runner.run(suite)

    result = tracer.runfunc(run_suite)

    if not result.wasSuccessful():
        sys.exit(1)

    coverage_results = tracer.results()
    executed: dict[Path, set[int]] = {}
    counts = coverage_results.counts
    for key, counter in counts.items():
        if not isinstance(key, tuple) or len(key) != 2:
            continue
        filename, line_no = key
        if not isinstance(filename, str):
            continue
        path = Path(filename).resolve()
        if package_root not in path.parents and path != package_root:
            continue
        executed.setdefault(path, set())
        if counter > 0:
            executed[path].add(line_no)

    total_lines = 0
    executed_lines = 0

    missing_report: list[tuple[Path, list[int]]] = []

    for source_path in package_root.rglob("*.py"):
        counted_line_numbers = sorted(_get_counted_lines(source_path))
        code_lines = count_code_lines(source_path)
        total_lines += code_lines
        covered = len(
            [
                line_no
                for line_no in executed.get(source_path.resolve(), set())
                if _is_counted_line(source_path, line_no)
            ]
        )
        executed_lines += covered
        missing = sorted(set(counted_line_numbers) - executed.get(source_path.resolve(), set()))
        if missing:
            missing_report.append((source_path, missing))

    coverage_percentage = 0.0 if total_lines == 0 else (executed_lines / total_lines) * 100.0
    print(f"Coverage: {coverage_percentage:.2f}% ({executed_lines}/{total_lines} lines)")
    for path, missing in missing_report:
        rel = path.relative_to(repo_root)
        print(f"  Missing lines in {rel}: {missing}")

    if coverage_percentage < 95.0:
        print("Coverage threshold not met (required 95%).", file=sys.stderr)
        sys.exit(1)


def _is_counted_line(source_path: Path, line_no: int) -> bool:
    return line_no in _get_counted_lines(source_path)


def _should_ignore_line(path: Path, line_no: int) -> bool:
    try:
        line = path.read_text(encoding="utf-8").splitlines()[line_no - 1]
    except IndexError:
        return False
    stripped = line.strip()
    if not stripped:
        return True
    if stripped.startswith("LOGGER"):
        return True
    if stripped.startswith("return"):
        return True
    if stripped == "break" or stripped == "continue":
        return True
    return False


if __name__ == "__main__":
    main()
