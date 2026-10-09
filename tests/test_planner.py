from some.planner import route_message


def test_routes_common_messages_without_an_ai_model(monkeypatch):
    monkeypatch.delenv("SOME_OLLAMA_MODEL", raising=False)
    assert route_message("show my files", [])["action"] == "list_files"
    assert route_message("find duplicate files", [])["action"] == "find_duplicates"
    assert route_message("organize my files", [])["action"] == "preview_organization"
    note = route_message("create note: buy milk", [])
    assert note == {"action": "create_note", "args": {"content": "buy milk"}}


def test_csv_selection_never_invents_a_filename(monkeypatch):
    monkeypatch.delenv("SOME_OLLAMA_MODEL", raising=False)
    route = route_message("clean customers.csv", ["sales.csv"])
    assert route["action"] == "choose_csv"
    assert route["args"]["file_error"] == "not_found"


def test_unknown_message_does_not_claim_work_was_done(monkeypatch):
    monkeypatch.delenv("SOME_OLLAMA_MODEL", raising=False)
    route = route_message("do something magical", [])
    assert route["action"] == "unknown"
