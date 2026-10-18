from agent.registry import load_boards
from agent.settings import ROOT


def test_shipped_registry_separates_verified_and_pending_sources():
    active, errors = load_boards()
    assert not errors
    assert all(board['verification_status'] == 'verified' for board in active if board['enabled'])
    pending, errors = load_boards(ROOT / 'config/company_boards.unverified.yaml')
    assert not errors
    assert all(not board['enabled'] for board in pending)
    assert not ({(b['platform'], b['board']) for b in active} &
                {(b['platform'], b['board']) for b in pending})
