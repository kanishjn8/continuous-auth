"""Generate deterministic Python and C++ bindings from the canonical JSON Schema."""

from __future__ import annotations

import argparse
import hashlib
import json
import keyword
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "protocol" / "schemas" / "contracts.schema.json"
VERSION_PATH = ROOT / "protocol" / "VERSION"
PYTHON_PATH = ROOT / "protocol" / "generated" / "python" / "contracts.py"
PYTHON_INIT_PATH = ROOT / "protocol" / "generated" / "python" / "__init__.py"
CPP_PATH = ROOT / "protocol" / "generated" / "cpp" / "contracts.hpp"


class UnsupportedSchema(ValueError):
    """Raised rather than silently weakening an unsupported schema construct."""


def _ref_name(reference: str) -> str:
    prefix = "#/$defs/"
    if not reference.startswith(prefix):
        raise UnsupportedSchema(f"external reference is not supported by codegen: {reference}")
    return reference.removeprefix(prefix)


def _python_constraint(schema: dict[str, Any]) -> str:
    mapping = {
        "minimum": "ge",
        "maximum": "le",
        "exclusiveMinimum": "gt",
        "exclusiveMaximum": "lt",
        "minLength": "min_length",
        "maxLength": "max_length",
        "pattern": "pattern",
        "minItems": "min_length",
        "maxItems": "max_length",
    }
    values = [
        f"{target}={schema[source]!r}" for source, target in mapping.items() if source in schema
    ]
    return ", ".join(values)


def _python_annotation(schema: dict[str, Any], *, constrain: bool = True) -> str:
    if "$ref" in schema:
        return _ref_name(schema["$ref"])
    if "const" in schema:
        return f"Literal[{schema['const']!r}]"
    if "enum" in schema:
        values = ", ".join(repr(value) for value in schema["enum"])
        return f"Literal[{values}]"
    for union_key in ("anyOf", "oneOf"):
        if union_key in schema:
            return " | ".join(
                _python_annotation(option, constrain=constrain) for option in schema[union_key]
            )

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        return " | ".join(
            _python_annotation({**schema, "type": item}, constrain=constrain)
            for item in schema_type
        )
    if schema_type == "string":
        annotation = "str"
    elif schema_type == "integer":
        annotation = "int"
    elif schema_type == "number":
        annotation = "float"
    elif schema_type == "boolean":
        annotation = "bool"
    elif schema_type == "null":
        annotation = "None"
    elif schema_type == "array":
        annotation = f"list[{_python_annotation(schema['items'])}]"
    elif schema_type == "object":
        additional = schema.get("additionalProperties", True)
        if additional is False and schema.get("properties"):
            raise UnsupportedSchema("anonymous closed objects must be promoted into $defs")
        value_type = "Any" if additional is True else _python_annotation(additional)
        annotation = f"dict[str, {value_type}]"
    else:
        raise UnsupportedSchema(f"unsupported schema: {schema}")

    constraints = _python_constraint(schema) if constrain else ""
    return f"Annotated[{annotation}, Field({constraints})]" if constraints else annotation


def _safe_python_name(name: str) -> str:
    return f"{name}_" if keyword.iskeyword(name) else name


def _cpp_enum_member(value: str) -> str:
    """Prefix enum members so Windows SDK macros (for example DELETE) cannot collide."""

    words = [word for word in re.split(r"[^A-Za-z0-9]+", value) if word]
    return "k" + "".join(word[:1].upper() + word[1:].lower() for word in words)


def _condition_expression(properties: dict[str, Any]) -> str:
    expressions: list[str] = []
    for field_name, condition in properties.items():
        access = f"_enum_value(self.{_safe_python_name(field_name)})"
        if "const" in condition:
            expressions.append(f"{access} == {condition['const']!r}")
        elif "enum" in condition:
            expressions.append(f"{access} in {tuple(condition['enum'])!r}")
        else:
            raise UnsupportedSchema(f"unsupported conditional predicate: {condition}")
    return " and ".join(expressions)


def _violation_expression(field_name: str, constraint: dict[str, Any]) -> str:
    access = f"self.{_safe_python_name(field_name)}"
    if constraint.get("type") == "null":
        return f"{access} is not None"
    if "$ref" in constraint:
        return f"not isinstance({access}, {_ref_name(constraint['$ref'])})"
    if "const" in constraint:
        return f"_enum_value({access}) != {constraint['const']!r}"
    if constraint.get("type") in {"number", "integer", "string", "boolean"}:
        return f"{access} is None"
    raise UnsupportedSchema(f"unsupported conditional consequence: {constraint}")


def _conditional_validator_lines(model_name: str, schema: dict[str, Any]) -> list[str]:
    clauses = schema.get("allOf", [])
    if not clauses:
        return []
    lines = [
        "",
        '    @model_validator(mode="after")',
        f"    def validate_schema_conditionals(self) -> {model_name}:",
    ]
    for clause in clauses:
        condition = clause.get("if", {}).get("properties")
        consequences = clause.get("then", {}).get("properties")
        if not isinstance(condition, dict) or not isinstance(consequences, dict):
            raise UnsupportedSchema(f"unsupported allOf conditional: {clause}")
        condition_expression = _condition_expression(condition)
        for field_name, constraint in consequences.items():
            violation = _violation_expression(field_name, constraint)
            lines.extend(
                [
                    f"        if {condition_expression} and {violation}:",
                    "            raise ValueError(",
                    f"                {f'{field_name} violates a conditional contract rule'!r}",
                    "            )",
                ]
            )
    lines.extend(["        return self", ""])
    return lines


def render_python(catalogue: dict[str, Any], digest: str, version: str) -> str:
    definitions = catalogue["$defs"]
    lines = [
        "# @generated by protocol/codegen/generate.py; DO NOT EDIT.",
        f"# schema-sha256: {digest}",
        '"""Pydantic v2 bindings generated from protocol v1 JSON Schema."""',
        "",
        "from __future__ import annotations",
        "",
        "from enum import Enum",
        "from typing import Annotated, Any, Literal, TypeAlias",
        "",
        "from pydantic import BaseModel, ConfigDict, Field, model_validator",
        "",
        "",
        "def _enum_value(value: Any) -> Any:",
        "    return value.value if isinstance(value, Enum) else value",
        "",
        f"PROTOCOL_VERSION: Literal[{version!r}] = {version!r}",
        "",
    ]
    model_names: list[str] = []
    for name, schema in definitions.items():
        if schema.get("type") == "string" and "enum" in schema:
            lines.append(f"class {name}(str, Enum):")
            for value in schema["enum"]:
                member = re.sub(r"[^A-Z0-9_]", "_", value.upper())
                lines.append(f"    {member} = {value!r}")
            lines.append("")
            continue
        if schema.get("type") == "object" and "properties" in schema:
            model_names.append(name)
            required = set(schema.get("required", []))
            lines.extend(
                [
                    f"class {name}(BaseModel):",
                    '    model_config = ConfigDict(extra="forbid", frozen=True)',
                    "",
                ]
            )
            for field_name, field_schema in schema["properties"].items():
                annotation = _python_annotation(field_schema)
                safe_name = _safe_python_name(field_name)
                if field_name not in required:
                    annotation = f"{annotation} | None"
                    lines.append(f"    {safe_name}: {annotation} = None")
                else:
                    lines.append(f"    {safe_name}: {annotation}")
            lines.extend(_conditional_validator_lines(name, schema))
            lines.append("")
            continue
        if "oneOf" in schema or "anyOf" in schema:
            lines.append(f"{name}: TypeAlias = {_python_annotation(schema)}")
            lines.append("")
            continue
        raise UnsupportedSchema(f"unsupported top-level definition {name}: {schema}")

    for model_name in model_names:
        lines.append(f"{model_name}.model_rebuild()")
    lines.append("")
    return "\n".join(lines)


def _cpp_type(schema: dict[str, Any]) -> str:
    if "$ref" in schema:
        return _ref_name(schema["$ref"])
    if "const" in schema or "enum" in schema:
        return "std::string"
    for union_key in ("anyOf", "oneOf"):
        if union_key in schema:
            options = schema[union_key]
            non_null = [option for option in options if option.get("type") != "null"]
            if len(non_null) == 1 and len(non_null) != len(options):
                return f"std::optional<{_cpp_type(non_null[0])}>"
            return f"std::variant<{', '.join(_cpp_type(option) for option in options)}>"
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        non_null_types = [item for item in schema_type if item != "null"]
        if len(non_null_types) == 1 and len(non_null_types) != len(schema_type):
            return f"std::optional<{_cpp_type({**schema, 'type': non_null_types[0]})}>"
        raise UnsupportedSchema(f"unsupported C++ type union: {schema_type}")
    if schema_type == "string":
        return "std::string"
    if schema_type == "integer":
        return "std::int64_t"
    if schema_type == "number":
        return "double"
    if schema_type == "boolean":
        return "bool"
    if schema_type == "null":
        return "std::monostate"
    if schema_type == "array":
        return f"std::vector<{_cpp_type(schema['items'])}>"
    if schema_type == "object":
        additional = schema.get("additionalProperties", True)
        value_type = "std::string" if additional is True else _cpp_type(additional)
        return f"std::map<std::string, {value_type}>"
    raise UnsupportedSchema(f"unsupported C++ schema: {schema}")


def render_cpp(catalogue: dict[str, Any], digest: str, version: str) -> str:
    lines = [
        "// @generated by protocol/codegen/generate.py; DO NOT EDIT.",
        f"// schema-sha256: {digest}",
        "#pragma once",
        "",
        "#include <cstdint>",
        "#include <map>",
        "#include <optional>",
        "#include <string>",
        "#include <string_view>",
        "#include <variant>",
        "#include <vector>",
        "",
        "namespace continuous_auth::protocol::v1 {",
        "",
        f'inline constexpr std::string_view kProtocolVersion{{"{version}"}};',
        "",
    ]
    for name, schema in catalogue["$defs"].items():
        if schema.get("type") == "string" and "enum" in schema:
            lines.append(f"enum class {name} {{")
            for value in schema["enum"]:
                member = _cpp_enum_member(value)
                lines.append(f"  {member},")
            lines.extend(["};", ""])
            continue
        if schema.get("type") == "object" and "properties" in schema:
            required = set(schema.get("required", []))
            lines.append(f"struct {name} {{")
            for field_name, field_schema in schema["properties"].items():
                field_type = _cpp_type(field_schema)
                if field_name not in required and not field_type.startswith("std::optional<"):
                    field_type = f"std::optional<{field_type}>"
                lines.append(f"  {field_type} {field_name};")
            lines.extend(["};", ""])
            continue
        if "oneOf" in schema or "anyOf" in schema:
            lines.extend([f"using {name} = {_cpp_type(schema)};", ""])
            continue
        raise UnsupportedSchema(f"unsupported top-level definition {name}: {schema}")
    lines.extend(["}  // namespace continuous_auth::protocol::v1", ""])
    return "\n".join(lines)


def _write_or_check(path: Path, expected: str, check: bool) -> bool:
    if check:
        actual = path.read_text(encoding="utf-8") if path.exists() else ""
        if actual != expected:
            print(f"generated binding is stale: {path.relative_to(ROOT)}", file=sys.stderr)
            return False
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(expected, encoding="utf-8", newline="\n")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if generated files differ")
    args = parser.parse_args()

    schema_bytes = SCHEMA_PATH.read_bytes()
    catalogue = json.loads(schema_bytes)
    version = VERSION_PATH.read_text(encoding="utf-8").strip()
    declared_versions: set[str] = set()

    def collect_declared_versions(value: object) -> None:
        if isinstance(value, dict):
            properties = value.get("properties")
            if isinstance(properties, dict):
                for field_name in ("schema_version", "protocol_version"):
                    field_schema = properties.get(field_name)
                    if isinstance(field_schema, dict) and isinstance(
                        field_schema.get("const"), str
                    ):
                        declared_versions.add(field_schema["const"])
            for child in value.values():
                collect_declared_versions(child)
        elif isinstance(value, list):
            for child in value:
                collect_declared_versions(child)

    collect_declared_versions(catalogue)
    if declared_versions != {version}:
        print(
            f"protocol/VERSION {version!r} does not match schema constants "
            f"{sorted(declared_versions)!r}",
            file=sys.stderr,
        )
        return 1
    digest = hashlib.sha256(schema_bytes + b"\0" + version.encode("utf-8")).hexdigest()
    outputs = {
        PYTHON_PATH: render_python(catalogue, digest, version),
        PYTHON_INIT_PATH: (
            "# @generated by protocol/codegen/generate.py; DO NOT EDIT.\n"
            "from .contracts import *  # noqa: F403\n"
        ),
        CPP_PATH: render_cpp(catalogue, digest, version),
    }
    results = [_write_or_check(path, content, args.check) for path, content in outputs.items()]
    if not all(results):
        print("run: python protocol/codegen/generate.py", file=sys.stderr)
        return 1
    if not args.check:
        for path in outputs:
            print(f"generated {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
