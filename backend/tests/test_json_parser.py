import pytest

from backend.llm.json_parser import JSONParseError, parse_json_object


def test_parse_pure_json():
    data = parse_json_object('{"name": "test", "value": 1}')

    assert data["name"] == "test"
    assert data["value"] == 1


def test_parse_fenced_json():
    data = parse_json_object(
        """```json
{"name": "test", "value": 1}
```"""
    )

    assert data["name"] == "test"
    assert data["value"] == 1


def test_parse_json_with_explanatory_text():
    data = parse_json_object(
        """
Here is the requested JSON:

{
  "name": "test",
  "value": 1
}

Hope this helps.
"""
    )

    assert data["name"] == "test"
    assert data["value"] == 1


def test_parse_json_with_trailing_comma():
    data = parse_json_object(
        """
{
  "name": "test",
  "items": ["A", "B",],
}
"""
    )

    assert data["name"] == "test"
    assert data["items"] == ["A", "B"]


def test_reject_empty_text():
    with pytest.raises(JSONParseError):
        parse_json_object("   ")


def test_reject_non_json_text():
    with pytest.raises(JSONParseError):
        parse_json_object("This is not JSON.")


def test_reject_json_array():
    with pytest.raises(JSONParseError):
        parse_json_object('[{"name": "test"}]')