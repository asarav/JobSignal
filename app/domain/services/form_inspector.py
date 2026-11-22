"""DOM → ``FormModel`` (spec §13.2/§13.3), without running page scripts.

The inspector parses the HTML the page already produced. It never evaluates
JavaScript, never clicks and never reads state back from the live form — that
keeps inspection a pure function of the markup, which makes every safety test
deterministic and keeps the domain free of browser capabilities.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from html.parser import HTMLParser

from app.domain.models.browser import (
    FieldOption,
    FormControl,
    FormField,
    FormModel,
)
from app.domain.services.field_classifier import classify_field
from app.domain.services.submission_guard import DEFAULT_SUBMIT_LEXICON

VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}

INPUT_KINDS: Mapping[str, str] = {
    "text": "text",
    "email": "email",
    "tel": "tel",
    "url": "url",
    "number": "number",
    "date": "date",
    "search": "text",
    "password": "unknown",
    "checkbox": "checkbox",
    "radio": "radio_group",
    "file": "file",
}

NEXT_TEXT = re.compile(r"\b(next|continue|proceed|weiter|suivant)\b", re.IGNORECASE)
BACK_TEXT = re.compile(r"\b(back|previous|zurück|précédent)\b", re.IGNORECASE)
HIDDEN_CLASS = re.compile(r"(honeypot|hidden|invisible|offscreen)", re.IGNORECASE)

#: Cards that put their question in a block of their own instead of a ``<label>``
#: (Lever: ``<div class="application-label">…</div>`` beside the control list).
QUESTION_BLOCK_CLASS = re.compile(r"(label|question|prompt)", re.IGNORECASE)

#: How far above a choice control the inspector looks for that sibling block.
MAX_QUESTION_DEPTH = 6

CHOICE_INPUT_TYPES = frozenset({"checkbox", "radio"})


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    parent: _Node | None = None
    children: list[_Node] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)

    def text(self) -> str:
        # Iterative document-order walk: a page can nest thousands of elements,
        # and recursion would turn that into a RecursionError.
        parts: list[str] = []
        stack: list[_Node] = [self]
        while stack:
            node = stack.pop()
            parts.extend(part for part in node.text_parts if part.strip())
            stack.extend(reversed(node.children))
        return re.sub(r"\s+", " ", " ".join(parts).strip())


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#document")
        self._stack: list[_Node] = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag, {key: value or "" for key, value in attrs}, self._stack[-1])
        self._stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag, {key: value or "" for key, value in attrs}, self._stack[-1])
        self._stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self._stack[-1].text_parts.append(data)


def _walk(node: _Node) -> Iterable[_Node]:
    """Depth-first, document order, without recursion."""
    stack: list[_Node] = list(reversed(node.children))
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", value).strip()
    return text or None


def _hidden(node: _Node) -> bool:
    if "hidden" in node.attrs:
        return True
    if node.attrs.get("aria-hidden", "").lower() == "true":
        return True
    if node.attrs.get("type", "").lower() == "hidden":
        return True
    style = node.attrs.get("style", "").replace(" ", "").lower()
    if "display:none" in style or "visibility:hidden" in style:
        return True
    return bool(HIDDEN_CLASS.search(node.attrs.get("class", "")))


def _ancestor(node: _Node, tag: str) -> _Node | None:
    current = node.parent
    while current is not None:
        if current.tag == tag:
            return current
        current = current.parent
    return None


#: Elements a ``<label>`` can be the label of. ``button``/``meter``/``output``
#: are labelable per WHATWG but never carry an answer, so they do not count as
#: "the control this label belongs to".
LABEL_CONTROL_TAGS = frozenset({"input", "select", "textarea"})


def _is_label_control(node: _Node) -> bool:
    if node.tag not in LABEL_CONTROL_TAGS:
        return False
    return (node.attrs.get("type") or "").lower() != "hidden"


def _wrapping_label(node: _Node) -> _Node | None:
    """The nearest ancestor ``<label>``, however deeply the control is nested.

    Real ATS markup puts markup between the label and its control (Lever uses
    ``<label><div>Full name</div><div><input></div></label>``). Checking only
    the direct parent lost those labels, so required fields like "Full name"
    were classified as unrecognised.
    """
    current = node.parent
    while current is not None:
        if current.tag == "label":
            return current
        current = current.parent
    return None


def _contains(parent: _Node, node: _Node) -> bool:
    current: _Node | None = node
    while current is not None:
        if current is parent:
            return True
        current = current.parent
    return False


def _label_controls(label: _Node) -> list[_Node]:
    return [node for node in _walk(label) if _is_label_control(node)]


def _label_text_for(label: _Node, node: _Node) -> str:
    """Label text with the control's own subtree left out.

    Without the exclusion a ``<select>`` wrapped in its label would contribute
    every option's text, and a nested input's sibling markup would leak in.
    Document order is preserved.
    """
    parts: list[str] = []
    stack: list[_Node] = [label]
    while stack:
        current = stack.pop()
        parts.extend(part for part in current.text_parts if part.strip())
        stack.extend(
            reversed([child for child in current.children if not _contains(child, node)])
        )
    return " ".join(parts)


def _question_context(node: _Node) -> str | None:
    """The question a card-style choice group answers, when it has one.

    Lever's additional cards are ``<div class="application-label">Are you
    eligible to work in the United States?</div>`` followed by the control list
    in a sibling div, so the checkbox's own wrapping label can only ever say
    "Yes". Only a sibling that marks itself as a label/question block counts,
    and it must not contain a control of its own, so a section heading ("Links")
    is never mistaken for a question. Nothing here changes what may be filled:
    the question only lets the classifier see which question the box answers.
    """
    if node.tag != "input":
        return None
    if (node.attrs.get("type") or "").lower() not in CHOICE_INPUT_TYPES:
        return None
    current = node.parent
    for _ in range(MAX_QUESTION_DEPTH):
        if current is None or current.tag in {"form", "fieldset"}:
            return None
        branch = next(
            (index for index, child in enumerate(current.children) if _contains(child, node)),
            None,
        )
        if branch is not None:
            for sibling in reversed(current.children[:branch]):
                if _hidden(sibling):
                    continue
                if any(_is_label_control(descendant) for descendant in _walk(sibling)):
                    continue
                marked = bool(
                    QUESTION_BLOCK_CLASS.search(sibling.attrs.get("class", ""))
                )
                if not marked and sibling.tag != "legend":
                    continue
                text = _clean(sibling.text())
                if text:
                    return text
        current = current.parent
    return None


def _option_only_label(node: _Node, label: str | None) -> bool:
    """Whether ``label`` is just the control's own option text ("Yes")."""
    value = _clean(node.attrs.get("value"))
    if label is None:
        return True
    if value is None:
        return False
    return label.strip().casefold() == value.casefold()


def _checkbox_option(node: _Node, labels: _LabelIndex) -> tuple[FieldOption, ...]:
    """A checkbox is a one-option choice; keep that option for the planner."""
    value = node.attrs.get("value", "")
    text = labels.for_node(node) or value
    return (
        FieldOption(value=value, label=text, selected="checked" in node.attrs),
    )


def _section_hint(node: _Node) -> str | None:
    current = node.parent
    while current is not None:
        if current.tag == "fieldset":
            for child in current.children:
                if child.tag == "legend":
                    return _clean(child.text())
        section = current.attrs.get("data-section")
        if section:
            return _clean(section)
        current = current.parent
    return None


SIMPLE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def _css_string(value: str) -> str:
    """Escape page-supplied text for use inside a quoted CSS attribute value.

    ``id``/``name`` values come from the page. Interpolating them raw would let
    a hostile field break out of the selector and point the filler at another
    element (including a non-form one), so every quote, backslash and newline
    is escaped before the selector is built.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return escaped.replace("\n", "\\a ").replace("\r", "\\d ").replace("\f", "\\c ")


def _selector(node: _Node, index: int) -> str:
    node_id = node.attrs.get("id")
    if node_id:
        if SIMPLE_ID.match(node_id):
            return f"#{node_id}"
        return f'[id="{_css_string(node_id)}"]'
    name = node.attrs.get("name")
    if name:
        value = node.attrs.get("value")
        if (node.attrs.get("type") or "").lower() in CHOICE_INPUT_TYPES and value:
            # "Yes"/"No" boxes can share one name; the value is the only thing
            # that tells them apart, so a fill cannot land on the other option.
            return f'[name="{_css_string(name)}"][value="{_css_string(value)}"]'
        return f'[name="{_css_string(name)}"]'
    form = _ancestor(node, "form")
    form_id = form.attrs.get("id") if form is not None else None
    if form_id and SIMPLE_ID.match(form_id):
        prefix = f"#{form_id} "
    elif form_id:
        prefix = f'[id="{_css_string(form_id)}"] '
    else:
        prefix = ""
    return f"{prefix}{node.tag}:nth-of-type({index})"


class _LabelIndex:
    def __init__(self, root: _Node) -> None:
        self._nodes = list(_walk(root))
        self._by_for = {
            node.attrs["for"]: node
            for node in self._nodes
            if node.tag == "label" and node.attrs.get("for")
        }
        self._by_id = {
            node.attrs["id"]: node for node in self._nodes if node.attrs.get("id")
        }

    def for_node(self, node: _Node) -> str | None:
        node_id = node.attrs.get("id")
        if node_id and node_id in self._by_for:
            return _clean(self._by_for[node_id].text())
        label = _wrapping_label(node)
        if label is None:
            return None
        # A label labels exactly one control. With several labelable
        # descendants the markup is ambiguous, and attaching the whole block's
        # text to one control would mislabel it, so no label is better.
        if len(_label_controls(label)) != 1:
            return None
        return _clean(_label_text_for(label, node))

    def by_id(self, element_id: str) -> str | None:
        node = self._by_id.get(element_id)
        return _clean(node.text()) if node is not None else None


def _accessible_name(node: _Node, labels: _LabelIndex, label: str | None) -> str | None:
    aria_label = _clean(node.attrs.get("aria-label"))
    if aria_label:
        return aria_label
    labelledby = node.attrs.get("aria-labelledby")
    if labelledby:
        names = [labels.by_id(part) for part in labelledby.split()]
        joined = _clean(" ".join(name for name in names if name))
        if joined:
            return joined
    return label


def _description(node: _Node, labels: _LabelIndex) -> str | None:
    describedby = node.attrs.get("aria-describedby")
    if describedby:
        parts = [labels.by_id(token) for token in describedby.split()]
        joined = _clean(" ".join(part for part in parts if part))
        if joined:
            return joined
    return _clean(node.attrs.get("title"))


def _options(node: _Node) -> tuple[FieldOption, ...]:
    out: list[FieldOption] = []
    for option in node.children:
        if option.tag != "option":
            continue
        out.append(
            FieldOption(
                value=option.attrs.get("value", "") or _clean(option.text()) or "",
                label=_clean(option.text()) or "",
                selected="selected" in option.attrs,
            )
        )
    return tuple(out)


def _radio_options(nodes: list[_Node], labels: _LabelIndex) -> tuple[FieldOption, ...]:
    out: list[FieldOption] = []
    for node in nodes:
        label = labels.for_node(node)
        out.append(
            FieldOption(
                value=node.attrs.get("value", ""),
                label=label or node.attrs.get("value", ""),
                selected="checked" in node.attrs,
            )
        )
    return tuple(out)


def _control_role(
    text: str,
    type_attr: str | None,
    lexicon: Iterable[str],
    signals: Iterable[str | None] = (),
) -> str:
    if (type_attr or "").lower() in {"submit", "image"}:
        return "submit"
    # §13.5.3: text, value, aria-label, title, id/name all count.
    for signal in (text, *signals):
        lowered = (signal or "").strip().lower()
        if not lowered:
            continue
        for term in lexicon:
            token = term.strip().lower()
            if token and re.search(rf"(?<![a-z]){re.escape(token)}(?![a-z])", lowered):
                return "submit"
    lowered = text.strip().lower()
    if NEXT_TEXT.search(lowered):
        return "next"
    if BACK_TEXT.search(lowered):
        return "back"
    return "other"


def inspect_html(
    html: str,
    *,
    session_id: str,
    url: str,
    lexicon: Iterable[str] = DEFAULT_SUBMIT_LEXICON,
) -> FormModel:
    """Parse ``html`` into a ``FormModel``; never raises on malformed markup."""
    builder = _Builder()
    builder.feed(html or "")
    builder.close()
    root = builder.root
    labels = _LabelIndex(root)

    forms = [node for node in _walk(root) if node.tag == "form"]
    steps = [form for form in forms if "data-step" in form.attrs]
    step_count = len(steps) or 1
    scopes: list[_Node] = forms or [root]

    fields: list[FormField] = []
    controls: list[FormControl] = []
    seen_radios: set[tuple[int, str]] = set()
    #: Radio groups are indexed once; scanning the whole scope per radio made
    #: this quadratic on forms with many options.
    radio_groups: dict[tuple[int, str], list[_Node]] = {}
    for scope_index, scope in enumerate(scopes):
        for candidate in _walk(scope):
            if candidate.tag != "input":
                continue
            if (candidate.attrs.get("type") or "").lower() != "radio":
                continue
            key = (scope_index, candidate.attrs.get("name", ""))
            radio_groups.setdefault(key, []).append(candidate)
    index = 0

    for scope_index, scope in enumerate(scopes):
        for node in _walk(scope):
            tag = node.tag
            if tag not in {"input", "select", "textarea", "button", "a"}:
                continue
            if tag == "a" and node.attrs.get("role", "").lower() != "button":
                continue
            input_type = (node.attrs.get("type") or "").lower()
            if tag == "input" and input_type in {"submit", "button", "reset", "image"}:
                controls.append(
                    _control(node, labels, lexicon, _selector(node, index))
                )
                index += 1
                continue
            if tag == "button":
                controls.append(
                    _control(node, labels, lexicon, _selector(node, index))
                )
                index += 1
                continue
            if tag == "a":
                controls.append(_control(node, labels, lexicon, _selector(node, index)))
                index += 1
                continue

            selector = _selector(node, index)
            index += 1
            if tag == "select":
                kind = "combobox" if node.attrs.get("role") == "combobox" else "select"
                options = _options(node)
            elif tag == "textarea":
                kind = "textarea"
                options = ()
            elif input_type == "radio":
                group = node.attrs.get("name", "")
                key = (scope_index, group)
                if group and key in seen_radios:
                    continue
                if group:
                    seen_radios.add(key)
                group_nodes = radio_groups.get(key, [node])
                kind = "radio_group"
                options = _radio_options(group_nodes, labels)
                selector = (
                    f'input[name="{_css_string(group)}"]' if group else selector
                )
            else:
                kind = INPUT_KINDS.get(input_type, "unknown")
                options = _checkbox_option(node, labels) if kind == "checkbox" else ()

            label = labels.for_node(node)
            question = _question_context(node)
            if question is not None and (
                kind == "radio_group"
                or (kind == "checkbox" and _option_only_label(node, label))
            ):
                # A radio group's own label is just its first option; a card
                # question is the honest name for the field, and it is the only
                # signal that says what the answer means.
                label = question
            name_attr = node.attrs.get("name") or None
            id_attr = node.attrs.get("id") or None
            placeholder = _clean(node.attrs.get("placeholder"))
            accessible = _accessible_name(node, labels, label)
            classification = classify_field(
                kind=kind,
                label=label,
                accessible_name=accessible,
                name_attr=name_attr,
                id_attr=id_attr,
                autocomplete_attr=node.attrs.get("autocomplete"),
                placeholder=placeholder,
                description=_description(node, labels),
                section_hint=_section_hint(node),
                options=options,
            )
            fields.append(
                FormField(
                    field_id=f"{selector}#{index}",
                    kind=kind,
                    label=label,
                    accessible_name=accessible,
                    name_attr=name_attr,
                    id_attr=id_attr,
                    autocomplete_attr=node.attrs.get("autocomplete") or None,
                    aria={
                        key: value
                        for key, value in node.attrs.items()
                        if key.startswith("aria-")
                    },
                    description=_description(node, labels),
                    options=options,
                    required="required" in node.attrs
                    or node.attrs.get("aria-required") == "true",
                    section_hint=_section_hint(node),
                    classification=classification,
                    step_index=0,
                    selector=selector,
                    placeholder=placeholder,
                    hidden=_hidden(node),
                )
            )

    has_next = any(control.role == "next" for control in controls)
    has_submit = any(control.role == "submit" for control in controls)
    if has_next:
        is_final_step: bool | None = False
    elif has_submit:
        is_final_step = True
    else:
        is_final_step = None

    return FormModel(
        session_id=session_id,
        url=url,
        fields=tuple(fields),
        controls=tuple(controls),
        step_index=0,
        step_count=step_count,
        is_final_step=is_final_step,
    )


def _control(
    node: _Node, labels: _LabelIndex, lexicon: Iterable[str], selector: str
) -> FormControl:
    text = (
        _clean(node.text())
        or _clean(node.attrs.get("value"))
        or _clean(node.attrs.get("aria-label"))
        or _clean(node.attrs.get("title"))
        or ""
    )
    type_attr = node.attrs.get("type")
    id_attr = node.attrs.get("id") or None
    name_attr = node.attrs.get("name") or None
    value_attr = node.attrs.get("value") or None
    aria_label = node.attrs.get("aria-label") or None
    title_attr = node.attrs.get("title") or None
    role = _control_role(
        text,
        type_attr,
        lexicon,
        (value_attr, aria_label, title_attr, id_attr, name_attr),
    )
    confidence = 0.0
    if role == "next":
        confidence = 0.95 if NEXT_TEXT.fullmatch(text.strip().lower()) else 0.88
    elif role == "submit":
        confidence = 1.0
    return FormControl(
        control_id=f"{selector}#{role}",
        text=text,
        role=role,
        tag=node.tag,
        type_attr=type_attr,
        confidence=confidence,
        selector=selector,
        value_attr=value_attr,
        aria_label=aria_label,
        title_attr=title_attr,
        id_attr=id_attr,
        name_attr=name_attr,
    )


def classify_only(html: str) -> tuple[FormField, ...]:
    """Convenience for tests: the fields of a document, no session needed."""
    return tuple(inspect_html(html, session_id="inspect", url="about:blank").fields)
