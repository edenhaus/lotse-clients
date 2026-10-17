"""The command generator: the committed module is its output, and every command is a method.

mypy already fails when a generated method passes a field its command class
no longer has; these tests fail when `_commands.py` is stale against the
bundle and the models, and cover what the generator refuses.
"""

import inspect
import json
from annotationlib import Format
from typing import TYPE_CHECKING, Any

import pytest

from lotse_client import API_VERSION
from lotse_client._commands import Commands
from scripts import generate_commands as generator
from tests.helpers import BUNDLE

if TYPE_CHECKING:
    from pathlib import Path

    from mashumaro.codecs.basic import BasicDecoder

    from lotse_client._base import Model
    from lotse_client.subscription import Subscription

RAW: dict[str, Any] = json.loads(BUNDLE.read_text())
MODELS_SOURCE = generator.MODELS.read_text()

TINY_MODELS = '''
from dataclasses import dataclass
from typing import Literal

type Mode = Literal["a", "b"]

@dataclass
class Empty:
    """Nothing."""

@dataclass
class ThingDoCommand:
    id: int
    thing_id: str
    type: Literal["thing/do"] = "thing/do"
    mode: Mode = "a"

@dataclass
class ThingResult:
    count: int

@dataclass
class OddCommand:
    id: int
    odd: Oddity
'''
"""A `models.py` of one namespaced command, for the generator's own tests."""


def tiny_bundle(
    command: str = "thing/do",
    *,
    title: str = "ThingDoCommand",
    properties: dict[str, Any] | None = None,
    result: str | None = "Success_for_ThingResult",
    event: str | None = None,
) -> dict[str, Any]:
    """A bundle of one command, answered by `result` and streaming `event`."""
    if properties is None:
        properties = {"thing_id": {"description": "The thing."}, "mode": {}}
    variant = {
        "title": title,
        "description": "Does a thing",
        "properties": {"id": {}, "type": {"const": command}, **properties},
    }
    defs = {
        "Command": {"oneOf": [variant]},
        "Empty": {"type": "object"},
        "ThingResult": {"type": "object", "properties": {"count": {}}},
        "Success_for_ThingResult": {"properties": {"result": {"$ref": "#/$defs/ThingResult"}}},
        "Success_for_Empty": {"properties": {"result": {"$ref": "#/$defs/Empty"}}},
        "Success_for_Nowhere": {"properties": {"result": {"$ref": "#/$defs/Nowhere"}}},
        "EventFrame_for_Mode": {"properties": {"event": {"$ref": "#/$defs/Mode"}}},
        "EventFrame_for_Nowhere": {"properties": {"event": {"$ref": "#/$defs/Nowhere"}}},
    }
    return {
        "$defs": defs,
        "results": {} if result is None else {command: {"$ref": f"#/$defs/{result}"}},
        "events": {} if event is None else {command: {"$ref": f"#/$defs/{event}"}},
    }


def command_variants() -> dict[str, dict[str, Any]]:
    """Every command's schema in the bundle, by its `type`."""
    command = RAW["$defs"]["Command"]
    return {v["properties"]["type"]["const"]: v for v in command["oneOf"]}


class RecordedError(Exception):
    """What `Recorder` raises in place of sending a command."""


class Recorder(Commands):
    """The generated base class on a transport that records each command instead of sending it."""

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[tuple[str, Model]] = []
        """Each command with the primitive it went through, `request` or `subscribe`."""

    async def _request(self, command: Model) -> dict[str, Any]:
        self.sent.append(("request", command))
        raise RecordedError

    async def _subscribe[E](self, command: Model, events: BasicDecoder[E]) -> Subscription[E]:
        del events  # never used: nothing is sent, so no event arrives
        self.sent.append(("subscribe", command))
        raise RecordedError


def test_the_committed_module_is_what_the_bundle_and_the_models_generate() -> None:
    assert generator.generate(RAW, MODELS_SOURCE) == generator.OUTPUT.read_text()


async def test_every_command_has_a_method_sending_its_command_with_every_field() -> None:
    # Every method, whatever the bundle adds: one a sync brings needs no test of its own.
    client = Recorder()
    for name, variant in command_variants().items():
        namespace, slash, verb = name.partition("/")
        owner = getattr(client, namespace) if slash else client
        method = getattr(owner, verb.replace("/", "_") if slash else name, None)
        assert method is not None, f"no method for {name}"
        fields = set(variant["properties"]) - {"id", "type"}
        parameters = set(inspect.signature(method, annotation_format=Format.FORWARDREF).parameters)
        assert parameters == fields, name
        arguments = {field: object() for field in fields}
        with pytest.raises(RecordedError):
            await method(**arguments)
        primitive, command = client.sent.pop()
        assert primitive == ("subscribe" if name in RAW["events"] else "request"), name
        assert type(command).__name__ == variant["title"]
        assert {field: getattr(command, field) for field in fields} == arguments, name
    assert not client.sent


def test_the_api_version_is_the_bundles() -> None:
    assert RAW["api"] == API_VERSION


def test_main_writes_the_module_and_the_api_version_and_check_reports_a_stale_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "_commands.py"
    version = tmp_path / "version.py"
    version.write_text(generator.VERSION.read_text().replace(API_VERSION, "0.0.1"))
    arguments = [
        "--bundle",
        str(BUNDLE),
        "--models",
        str(generator.MODELS),
        "--output",
        str(output),
        "--version-module",
        str(version),
    ]
    assert generator.main([*arguments, "--check"]) == 1
    err = capsys.readouterr().err
    assert f"{output} is stale" in err
    assert f"{version} is stale" in err
    assert generator.main(arguments) == 0
    assert output.read_text() == generator.OUTPUT.read_text()
    assert version.read_text() == generator.VERSION.read_text()
    assert generator.main(arguments) == 0  # current: left alone
    assert generator.main([*arguments, "--check"]) == 0
    output.write_text("stale\n")
    assert generator.main([*arguments, "--check"]) == 1
    assert generator.main(arguments) == 0
    assert output.read_text() == generator.OUTPUT.read_text()


def test_main_reports_what_it_cannot_generate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = tmp_path / "api.json"
    bundle.write_text(json.dumps(tiny_bundle(title="Missing")))
    models = tmp_path / "models.py"
    models.write_text(TINY_MODELS)
    output = tmp_path / "_commands.py"
    arguments = ["--bundle", str(bundle), "--models", str(models), "--output", str(output)]
    arguments += ["--version-module", str(tmp_path / "version.py")]
    assert generator.main(arguments) == 2
    assert "no class Missing" in capsys.readouterr().err
    assert not output.exists()


def test_the_api_version_line_is_set_to_the_bundles_and_must_be_there_once() -> None:
    source = '"""Doc."""\n\nAPI_VERSION = "0.1.0"\n"""Its docstring."""\nOTHER = 1\n'
    assert generator.with_api_version(source, "0.3.0") == source.replace("0.1.0", "0.3.0")
    assert generator.api_version({"api": "0.3.0"}) == "0.3.0"
    with pytest.raises(generator.GenerationError, match="names no API version"):
        generator.api_version({})
    with pytest.raises(generator.GenerationError, match="has 0 `API_VERSION"):
        generator.with_api_version("OTHER = 1\n", "0.3.0")
    with pytest.raises(generator.GenerationError, match="has 2 `API_VERSION"):
        generator.with_api_version(source + source, "0.3.0")


def test_a_namespaced_command_becomes_a_method_of_its_namespace() -> None:
    source = generator.generate(tiny_bundle(), TINY_MODELS)
    assert "class ThingCommands:" in source
    assert 'async def do(self, *, thing_id: str, mode: Mode = "a") -> ThingResult:' in source
    assert '"""Does a thing.\n\n        Args:\n            thing_id: The thing.\n' in source
    assert "            mode: `mode`.\n" in source  # an undescribed field
    assert "return ThingResult.from_dict(await self._request(command))" in source
    assert "self.thing = ThingCommands(self._request, self._subscribe)" in source
    assert (
        "from lotse_client.models import (\n    Mode,\n    ThingDoCommand,\n    ThingResult,\n)"
        in source
    )


def test_a_deeper_command_name_joins_its_verbs() -> None:
    source = generator.generate(tiny_bundle("thing/do/it"), TINY_MODELS)
    assert "async def do_it(self" in source
    assert "class ThingCommands:" in source


def test_an_empty_result_returns_none_and_a_subscribing_command_its_events() -> None:
    source = generator.generate(
        tiny_bundle(result="Success_for_Empty", event="EventFrame_for_Mode"), TINY_MODELS
    )
    assert "MODES: BasicDecoder[Mode] = BasicDecoder(Mode)" in source
    assert '"""Decodes the events of `thing/do`."""' in source
    assert ") -> Subscription[Mode]:" in source
    assert "return await self._subscribe(command, MODES)" in source
    plain = generator.generate(tiny_bundle(result="Success_for_Empty"), TINY_MODELS)
    assert ") -> None:" in plain
    assert "        await self._request(command)\n" in plain


def test_an_inline_result_schema_needs_no_reference() -> None:
    bundle = tiny_bundle()
    bundle["results"]["thing/do"] = {"properties": {"result": {}}}
    source = generator.generate(bundle, TINY_MODELS)
    assert ") -> dict[str, Any]:" in source
    assert "return await self._request(command)" in source


@pytest.mark.parametrize(
    ("bundle", "message"),
    [
        (tiny_bundle(title="Missing"), "no class Missing"),
        (tiny_bundle(properties={"nope": {}}), "has no field nope"),
        (tiny_bundle(result=None), "names no result for thing/do"),
        (tiny_bundle(result="Gone"), "no definition Gone"),
        (tiny_bundle(result="Success_for_Nowhere"), "Nowhere is not a class"),
        (tiny_bundle(event="EventFrame_for_Nowhere"), "event type Nowhere"),
        (tiny_bundle("thing/import"), "Python keyword"),
        (
            tiny_bundle(
                "odd", title="OddCommand", properties={"odd": {}}, result="Success_for_Empty"
            ),
            "Oddity.*which models.py does not define",
        ),
        (tiny_bundle("thing/do it"), "ruff format failed: .*Failed to parse"),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_the_generator_refuses_what_it_cannot_turn_into_a_method(
    bundle: dict[str, Any], message: str
) -> None:
    with pytest.raises(generator.GenerationError, match=message):
        generator.generate(bundle, TINY_MODELS)


def test_the_summary_is_cut_to_one_line_at_a_sentence_a_clause_a_comma_or_the_name() -> None:
    split = generator.split_summary
    assert split("Short.", 20) == ("Short.", "")
    assert split("First one. Then more.", 12) == ("First one.", "Then more.")
    assert split("First one; then more.", 12) == ("First one.", "Then more.")
    assert split("First one, then more, and more.", 25) == ("First one, then more.", "And more.")
    assert split("`a/b`: a long one that fits, nowhere else.", 15) == (
        "`a/b`.",
        "A long one that fits, nowhere else.",
    )
    assert split("`a/b`: no comma anywhere at all.", 7) == ("`a/b`.", "No comma anywhere at all.")
    assert split("Nothing fits here at all", 5) == ("Nothing fits here at all", "")
    assert split("plain: no backticks here at all", 8) == ("plain: no backticks here at all", "")
    assert split("Far too long a first sentence. Then more.", 12) == (
        "Far too long a first sentence. Then more.",
        "",
    )


def test_a_docstring_wraps_its_paragraphs_and_ends_its_summary() -> None:
    second = "A second paragraph long enough to need wrapping at the width ruff formats to, surely."
    lines = generator.docstring(f"Does a thing\n\n{second}", (), "    ")
    assert lines[0] == '    """Does a thing.'
    assert lines[1] == ""
    assert all(len(line) <= generator.LINE_LENGTH for line in lines)
    assert lines[-1] == '    """'
    assert generator.docstring("One line", (), "") == ['"""One line."""']
