import pytest
from agent import settings


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-8-sig'])
def test_profile_file_roundtrip(tmp_path, monkeypatch, candidate, encoding):
    monkeypatch.setattr(settings, 'ROOT', tmp_path)
    (tmp_path / 'candidate_profile.json').write_text(candidate.model_dump_json(), encoding=encoding)
    assert settings.profile() == candidate


def test_missing_profile_has_local_instructions(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'ROOT', tmp_path)
    with pytest.raises(ValueError, match='candidate_profile.json.*WINDOWS_SETUP.md'):
        settings.profile()


def test_invalid_profile_does_not_disclose_values(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'ROOT', tmp_path)
    (tmp_path / 'candidate_profile.json').write_text('{"experience_years":"PRIVATE_VALUE"}', encoding='utf-8')
    with pytest.raises(ValueError) as exc:
        settings.profile()
    assert 'PRIVATE_VALUE' not in str(exc.value)
    assert 'schema' in str(exc.value)
