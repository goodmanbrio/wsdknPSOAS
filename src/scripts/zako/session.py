"""Blocking Zako discovery state machine."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Protocol

from src.scripts.zako.scanner import (
    ROOT_FILES_KEY,
    Inventory,
    ScanError,
    SelectionInvalidated,
    canonicalize_source_identifier,
    display_label,
    expand_selection,
    revalidate_selection,
    scan_source_root,
    validate_source_root,
)
from src.scripts.zako.selector import (
    BunragmanSelector,
    SelectionFormatError,
    Selector,
    parse_selection,
)


FAILURE = "source discovery loop failed"


class ZakoChannel(Protocol):
    """Minimal blocking output/input seam used by the session."""

    def print(self, message: str, markdown: bool = False) -> None:
        ...

    def input(self, question: str, markdown: bool = False) -> str | None:
        ...


class _SessionAbort(RuntimeError):
    pass


class ZakoSession:
    """Own one complete blocking discovery interaction."""

    def __init__(
        self,
        query: str,
        config: Any,
        channel: ZakoChannel,
        selector: Selector | None = None,
        source_root: Path | None = None,
    ) -> None:
        self.active_query = query.strip()
        self.config = config
        self.channel = channel
        self.selector = selector
        self.source_root = Path(
            source_root if source_root is not None else config.zako_source_root
        )
        self.inventory: Inventory | None = None
        self.selected: list[str] = []
        self.invalidated: list[str] = []

    def run(self) -> tuple[str, dict[str, list[str]]] | str:
        try:
            # Root validity is checked before query prompting, as specified.
            validate_source_root(self.source_root)
            self._ensure_query()
            self.inventory = scan_source_root(self.source_root)
            self.selected = self._select_sources()

            while True:
                if self.invalidated:
                    action = self._handle_invalidated()
                    if action == "exit":
                        return FAILURE
                    if action == "redo":
                        self._redo()
                    continue

                if not self.selected:
                    action = self._handle_no_selection()
                    if action == "exit":
                        return FAILURE
                    if action == "redo":
                        self._redo()
                    continue

                action = self._handle_confirmation()
                if action == "exit":
                    return FAILURE
                if action == "redo":
                    self._redo()
                elif action == "confirmed":
                    return self._confirm_and_expand()
        except (ScanError, SelectionFormatError, SelectionInvalidated):
            return FAILURE
        except Exception:
            # Zako's public failure contract intentionally hides infrastructure
            # details and never emits a partial source dictionary.
            return FAILURE

    def _ensure_query(self) -> None:
        while not self.active_query:
            answer = self._ask(
                "Please provide a non-empty research query, or type `exit`."
            )
            if answer is None or self._normalize(answer) == "exit":
                raise _SessionAbort()
            if not answer.strip():
                self._write("The query cannot be empty.")
                continue
            self.active_query = answer.strip()

    def _get_selector(self) -> Selector:
        if self.selector is None:
            self.selector = BunragmanSelector(self.config)
        return self.selector

    def _select_sources(self) -> list[str]:
        if self.inventory is None:
            raise ScanError("inventory is not initialized")
        raw = self._get_selector()(self.active_query, self.inventory.payload)
        return parse_selection(raw, set(self.inventory.candidates))

    def _redo(self) -> None:
        answer = self._ask(
            "Enter the replacement query, or type `exit` to stop."
        )
        while answer is not None and not answer.strip():
            self._write("The replacement query cannot be empty.")
            answer = self._ask(
                "Enter the replacement query, or type `exit` to stop."
            )
        if answer is None or self._normalize(answer) == "exit":
            raise _SessionAbort()

        self.active_query = answer.strip()
        self.inventory = scan_source_root(self.source_root)
        self.selected = self._select_sources()
        self.invalidated = []

    def _handle_confirmation(self) -> str:
        if self.inventory is None:
            raise ScanError("inventory is not initialized")
        answer = self._ask(self._confirmation_text())
        command = self._normalize(answer)
        if command == "exit":
            return "exit"
        if command == "redo":
            return "redo"
        if command == "modify":
            return self._modify_flow()
        if command == "confirm":
            return "confirmed" if self._revalidate() else "invalidated"

        self._write(
            "I’m not sure what you want. Please choose `y`, `modify`, "
            "or `redo`."
        )
        return "clarify"

    def _handle_no_selection(self) -> str:
        answer = self._ask(
            "I did not find a relevant source group for this query.\n\n"
            "[redo] Re-read folder and filename names, then try again\n"
            "[modify] Add or remove candidate sources manually\n"
            "[exit] Stop discovery"
        )
        command = self._normalize(answer)
        if command == "exit":
            return "exit"
        if command == "redo":
            return "redo"
        if command == "modify":
            return self._modify_flow()
        self._write(
            "Please choose `redo`, `modify`, or `exit`."
        )
        return "clarify"

    def _handle_invalidated(self) -> str:
        names = ", ".join(f"`{name}`" for name in self.invalidated)
        answer = self._ask(
            f"The selected source(s) {names} are no longer available.\n\n"
            "[modify] Update the selected sources\n"
            "[redo] Re-read the directory names and select again\n"
            "[exit] Stop discovery"
        )
        command = self._normalize(answer)
        if command == "exit":
            return "exit"
        if command == "redo":
            return "redo"
        if command == "modify":
            return self._modify_flow()
        self._write("Please choose `modify`, `redo`, or `exit`.")
        return "clarify"

    def _modify_flow(self) -> str:
        if self.inventory is None:
            raise ScanError("inventory is not initialized")

        while True:
            available = ", ".join(
                f"`{identifier}`" for identifier in self.inventory.candidates
            )
            answer = self._ask(
                "Describe the changes using `add` and/or `remove`.\n"
                f"Available top-level sources: {available}\n"
                "Example: `add COHR and remove LITE`\n"
                "Type `redo` or `exit` at any time."
            )
            if answer is None:
                return "exit"
            command = self._normalize(answer)
            if command == "exit":
                return "exit"
            if command == "redo":
                return "redo"

            if answer.strip().casefold() in {"add", "remove"}:
                operation = answer.strip().casefold()
                operand = self._ask(
                    f"Which top-level source(s) should I {operation}?"
                )
                if operand is None:
                    return "exit"
                answer = f"{operation} {operand}"

            try:
                operations = self._parse_modification(answer)
                self._apply_modification(operations)
            except ValueError as exc:
                self._write(f"I could not apply that change: {exc}")
                continue

            self.invalidated = [
                identifier
                for identifier in self.selected
                if identifier not in self.inventory.candidates
            ]
            return "review"

    def _parse_modification(self, text: str) -> list[tuple[str, str]]:
        if self.inventory is None:
            raise ValueError("inventory is not initialized")
        matches = list(re.finditer(r"\b(add|remove)\b", text, re.I))
        if not matches:
            raise ValueError("use `add <source>` and/or `remove <source>`")

        operations: list[tuple[str, str]] = []
        for index, match in enumerate(matches):
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            segment = text[start:end]
            segment = re.sub(r"\band\b", ",", segment, flags=re.I)
            operands = [part.strip() for part in segment.split(",") if part.strip()]
            if not operands:
                raise ValueError(f"no source named after `{match.group(1)}`")
            for operand in operands:
                identifier = self._resolve_modification_operand(operand)
                operations.append((match.group(1).casefold(), identifier))

        if len({identifier for _, identifier in operations}) != len(operations):
            raise ValueError("do not repeat a source in one modification")
        return operations

    def _resolve_modification_operand(self, raw: str) -> str:
        if self.inventory is None:
            raise ValueError("inventory is not initialized")
        value = raw.strip().strip(" .,;:[]()")
        value = re.sub(r"^(?:the\s+)", "", value, flags=re.I)
        value = re.sub(r"\s+(?:folder|source|group)$", "", value, flags=re.I)

        # A selected-but-now-missing source remains removable after
        # revalidation; it cannot be added because it is not in the current
        # inventory.
        if value in self.selected and value not in self.inventory.candidates:
            return value
        return canonicalize_source_identifier(value, self.inventory)

    def _apply_modification(self, operations: list[tuple[str, str]]) -> None:
        if self.inventory is None:
            raise ValueError("inventory is not initialized")
        selected = list(self.selected)
        for operation, identifier in operations:
            if operation == "add":
                if identifier in selected:
                    raise ValueError(f"`{identifier}` is already selected")
                if identifier not in self.inventory.candidates:
                    raise ValueError(f"`{identifier}` is no longer available")
                selected.append(identifier)
            else:
                if identifier not in selected:
                    raise ValueError(f"`{identifier}` is not selected")
                selected.remove(identifier)
        self.selected = selected

    def _revalidate(self) -> bool:
        if self.inventory is None:
            raise ScanError("inventory is not initialized")
        current, missing = revalidate_selection(self.inventory, self.selected)
        self.inventory = current
        self.invalidated = missing
        return not missing

    def _confirm_and_expand(self) -> tuple[str, dict[str, list[str]]]:
        if self.inventory is None:
            raise ScanError("inventory is not initialized")
        return self.active_query, expand_selection(self.inventory, self.selected)

    def _confirmation_text(self) -> str:
        if self.inventory is None:
            raise ScanError("inventory is not initialized")
        lines = [
            "For this query, I found these sources:",
            "",
            f"Selected {len(self.selected)} sources:",
            "",
        ]
        for identifier in self.selected:
            candidate = self.inventory.candidates.get(identifier)
            if candidate is None:
                count = "unavailable"
            elif candidate.file_count is None:
                count = "unknown"
            else:
                count = f"{candidate.file_count} files"
            suffix = "" if identifier == ROOT_FILES_KEY else "/"
            lines.append(f"- {display_label(identifier)}{suffix} — {count}")
        lines.extend(
            [
                "",
                "Should I use these sources?",
                "",
                "[y] Confirm",
                "[modify] Add and/or remove source groups",
                "[redo] Discard this selection and generate a new one",
            ]
        )
        return "\n".join(lines)

    def _normalize(self, answer: str | None) -> str:
        if answer is None:
            return "exit"
        value = answer.strip().casefold()
        if not value:
            return "ambiguous"
        if value in {"y", "yes", "confirm", "confirmed", "use them", "use these"}:
            return "confirm"
        if value == "redo" or value.startswith("redo ") or value in {
            "start over", "start again", "restart",
        }:
            return "redo"
        if value == "modify" or value.startswith("modify ") or value in {
            "change", "change them", "change the sources", "add", "remove",
        }:
            return "modify"
        if value in {"exit", "quit", "stop", "cancel", "abort"}:
            return "exit"
        return "ambiguous"

    def _ask(self, prompt: str) -> str | None:
        return self.channel.input(prompt, markdown=True)

    def _write(self, message: str) -> None:
        self.channel.print(message, markdown=True)
