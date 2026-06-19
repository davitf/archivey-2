"""Griffe extensions that improve how the API reference renders.

Used by mkdocstrings (see ``mkdocs.yml``). Three small transforms:

- ``PropertyFieldExtension`` — fold ``@property`` accessors into the class's
  parameters/fields table (marked *(computed property)*) instead of giving each one its
  own section.
- ``RenameParametersSectionForDataclasses`` — title a dataclass's parameter table
  "Fields:" rather than "Parameters:".
- ``EnumMembersAsTable`` — render enum members as a Name/Value/Description table (the
  built-in Griffe sections render a "Type" column, which is wrong for enums). Also handles
  ``ArchiveFormat``, whose named ``ClassVar`` instances behave like enum members.
"""

from __future__ import annotations

from typing import Any

from griffe import (
    Attribute,
    Class,
    DocstringParameter,
    DocstringSectionParameters,
    ExprName,
    Extension,
)


class PropertyFieldExtension(Extension):
    def on_class_members(self, node: Any, cls: Class, agent: Any, **kwargs: Any) -> None:
        properties = {
            k: v for k, v in cls.attributes.items() if v.has_labels("property")
        }
        if not properties:
            return

        if cls.docstring and cls.docstring.parsed:
            parameters = [
                DocstringParameter(
                    name=k,
                    description="*(computed property)* "
                    + (v.docstring.value if v.docstring else ""),
                    annotation=v.annotation,
                )
                for k, v in properties.items()
            ]

            parameters_section = next(
                (
                    section
                    for section in cls.docstring.parsed
                    if isinstance(section, DocstringSectionParameters)
                ),
                None,
            )
            if not parameters_section:
                parameters_section = DocstringSectionParameters(value=[])
                cls.docstring.parsed.append(parameters_section)

            parameters_section.value.extend(parameters)

            # Remove properties from cls.members so they don't get separate sections.
            for name in properties:
                cls.members.pop(name, None)


class RenameParametersSectionForDataclasses(Extension):
    def on_class_instance(
        self, node: Any, cls: Class, agent: Any, **kwargs: Any
    ) -> None:
        if not cls.has_labels or not cls.has_labels("dataclass"):
            return

        if not cls.docstring or not cls.docstring.parsed:
            return

        for section in cls.docstring.parsed:
            if (
                isinstance(section, DocstringSectionParameters)
                and section.title is None
            ):
                section.title = "Fields:"


ENUM_BASES = {"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"}


def _is_enum_class(cls: Class) -> bool:
    # Any base canonical path matching Python enums.
    if any(getattr(b, "canonical_name", None) in ENUM_BASES for b in cls.bases or ()):
        return True

    flag = cls.members.get("__enum_like__")
    return isinstance(flag, Attribute) and flag.value in (True, "True")


class EnumMembersAsTable(Extension):
    # The built-in Griffe docstring sections are not appropriate for enum classes. The
    # closest, DocstringSectionOtherParameters, renders a "Type" column instead of a
    # "Value" one. So we stash the enum members in cls.extra and override the class
    # template (docs_templates/python/material/class.html.jinja) to render them as a table.

    def on_class_members(self, node: Any, cls: Class, agent: Any, **kwargs: Any) -> None:
        if not _is_enum_class(cls):
            return
        rows: list[dict[str, Any]] = []
        for name, m in list(cls.members.items()):
            if m.kind.value == "attribute" and not name.startswith("_"):
                assert isinstance(m, Attribute)
                # The second condition handles ArchiveFormat, which is not an enum class
                # but has ClassVar instances that act like enum values. The rendering is
                # imperfect (values come through empty) but better than nothing.
                if (m.value is not None and m.annotation is None) or (
                    isinstance(m.annotation, ExprName)
                    and m.annotation.canonical_path == cls.canonical_path
                ):
                    rows.append(
                        {
                            "name": name,
                            "value": m.value,
                            "doc": (m.docstring.value if m.docstring else ""),
                        }
                    )
                    cls.members.pop(name, None)
        if rows:
            cls.extra.setdefault("enum_members", {"rows": rows})
