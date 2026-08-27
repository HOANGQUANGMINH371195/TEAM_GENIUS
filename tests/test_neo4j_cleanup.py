import pytest

from database.neo4j.scripts.cleanup_stale_release import _validate_target


def test_cleanup_requires_exact_confirmation_and_distinct_retain_target():
    _validate_target("snapshot-old", "snapshot-current", "DELETE snapshot-old")
    with pytest.raises(ValueError, match="retained"):
        _validate_target("snapshot-current", "snapshot-current", "DELETE snapshot-current")
    with pytest.raises(ValueError, match="confirmation"):
        _validate_target("snapshot-old", "snapshot-current", "yes")
