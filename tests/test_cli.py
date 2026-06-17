import os.path

import pytest
from click.testing import CliRunner
from hooktest.cli import cli
from hooktest.tester import Result, Tester


def get_path(xml: str) -> str:
    return os.path.relpath(os.path.join(os.path.dirname(__file__), "test_data", xml))

def count_failing(result: Result) -> int:
    return len([s for s in result.statuses if s.status == False])


@pytest.fixture
def runner():
    return CliRunner()


def test_simple_passing_file(runner):
    """Test with a file expected to pass."""
    result = runner.invoke(cli, ['--no-catalog', get_path("correct_simple.xml")], standalone_mode=False)
    assert '✗' not in result.output, "File has a failing test"
    assert 'duplicateRefs[Tree=default]' not in result.output, "Tree Default has no dup reff"
    assert count_failing(result.return_value.results[get_path("correct_simple.xml")]) == 0, "Zero failing test"


def test_double_tree_file(runner):
    """Test with a file expected to pass."""
    result = runner.invoke(cli, ['--no-catalog', '-v', 'verbose', get_path("correct_double_tree.xml")], standalone_mode=False)
    assert '✗' not in result.output, "File has a failing test"
    assert "parse(citeStructures): Tree:default->line(15) " in result.output, "Both tree are documented"
    assert "Tree:translations->language(3)->[line(12)]" in result.output, "Both tree are documented"
    assert "forbiddenRefs[Tree=default]: ✔" in result.output, "Both tree are documented"
    assert "duplicateRefs[Tree=default]: ✔" in result.output, "Both tree are documented"
    assert "forbiddenRefs[Tree=translations]: ✔" in result.output, "Both tree are documented"
    assert "duplicateRefs[Tree=translations]: ✔" in result.output, "Both tree are documented"
    assert count_failing(result.return_value.results[get_path("correct_double_tree.xml")]) == 0, "Zero failing test"


def test_duplicate_refs(runner):
    """Test whether duplicate ref finding is working"""
    result = runner.invoke(cli, ['--no-catalog', get_path("duplicate.xml")], standalone_mode=False)
    assert '✗' in result.output, "File has a failing test"
    assert 'duplicateRefs[Tree=default]' in result.output, "Tree Default has duplicate reff"
    assert "`1`" in result.output, "Level 1 reference `1` is duplicated"
    assert "`1.2`" in result.output, "Level 2 reference `1.2` is duplicated within the first 1"
    assert "`1.3`" in result.output, "Level 2 reference `1.3` is duplicated across both 1"
    assert "`1.1`" not in result.output, "Level 2 reference `1.1` is not duplicated across both 1"
    assert count_failing(result.return_value.results[get_path("duplicate.xml")]) == 1, "Only one failing test"


def test_forbidden_ref(runner):
    """Test with a file expected to fail on forbidden refs."""
    result = runner.invoke(cli, ['--no-catalog', get_path("forbid.xml")], standalone_mode=False)
    assert '✗' in result.output, "File has a failing test"
    assert 'forbiddenRefs[Tree=default]' in result.output, "Tree Default has forbidden references"
    assert count_failing(result.return_value.results[get_path("forbid.xml")]) == 1, "Only one failing test"


def test_missing_delim_on_non_top_citestructure_is_reported(runner):
    """A citeStructure nested under another one must carry @delim (dapytains requires
    it to build its reference regex); this must be reported explicitly rather than
    surfacing as a cryptic parse exception."""
    result = runner.invoke(cli, ['--no-catalog', get_path("missing_delim.xml")], standalone_mode=False)
    assert '✗' in result.output, "File has a failing test"
    assert 'citeStructure/@delim' in result.output, "Missing @delim is reported under its own test name"
    assert 'section' in result.output, "The offending unit name is named in the details"
    assert isinstance(result.exception, SystemExit), "Failure must end the run gracefully, not crash"
    assert result.exit_code == 1


def test_malformed_file_does_not_crash_the_whole_run(runner):
    """A catastrophically broken file must be reported as a failing file, and must
    not abort testing of the other files in the batch."""
    result = runner.invoke(
        cli,
        ['--no-catalog', get_path("correct_simple.xml"), get_path("malformed.xml")],
        standalone_mode=False,
    )
    assert isinstance(result.exception, SystemExit), "Failure must end the run gracefully, not crash"
    assert result.exit_code == 1
    assert "Traceback" not in result.output, "No unhandled exception should leak into the output"
    assert os.path.relpath(get_path("correct_simple.xml")) in result.output
    assert os.path.relpath(get_path("malformed.xml")) in result.output


def test_manifest_lists_only_passing_files(runner, tmp_path):
    """-o/--manifest should write only the files whose Result.status is True."""
    manifest_path = tmp_path / "manifest.txt"
    runner.invoke(
        cli,
        [
            '--no-catalog',
            get_path("correct_simple.xml"),
            get_path("forbid.xml"),
            '-o', str(manifest_path),
        ],
        standalone_mode=False,
    )
    assert manifest_path.exists(), "Manifest file should have been written"
    manifest_lines = manifest_path.read_text().splitlines()
    assert get_path("correct_simple.xml") in manifest_lines, "Passing file is listed in the manifest"
    assert get_path("forbid.xml") not in manifest_lines, "Failing file is not listed in the manifest"


def test_catalog_schema_accepts_link_stub_member():
    """Regression test: a <collection>/<resource> member that only links to another
    catalog file via @filepath (no inline <title>) must validate against the schema,
    matching what dapytains itself accepts (e.g. First1KGreek's metadata.xml)."""
    tester = Tester()
    log = tester.run_catalog_schema(get_path("catalog_with_stub.xml"))
    assert log.status is True, log.details
