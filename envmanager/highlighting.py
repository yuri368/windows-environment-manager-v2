"""Readability aids for TOML and diffs; formatting never edits their text."""

from __future__ import annotations

import re

from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat


def _format(color: str, *, background: str | None = None, bold: bool = False):
    result = QTextCharFormat()
    result.setForeground(QColor(color))
    if background:
        result.setBackground(QColor(background))
    if bold:
        result.setFontWeight(QFont.Weight.DemiBold)
    return result


class TomlHighlighter(QSyntaxHighlighter):
    """A small lexical highlighter, independent of configuration validation."""

    _tokens = re.compile(
        r'(?P<comment>\#.*)|(?P<triple>"""|\'\'\')|'
        r'(?P<string>"(?:\\.|[^"\\])*"|\'[^\']*\')|'
        r'(?P<boolean>\b(?:true|false)\b)|(?P<number>\b\d+(?:\.\d+)?\b)'
    )
    _keys = re.compile(r'\b[\w-]+(?=\s*=)')

    def __init__(self, document):
        super().__init__(document)
        self.formats = {
            "comment": _format("#65715F"), "string": _format("#3A765B"),
            "boolean": _format("#895C32"), "number": _format("#725F83"),
            "key": _format("#766052"), "header": _format("#596F83", bold=True),
        }

    @staticmethod
    def _end(text: str, delimiter: str, start: int) -> int:
        while (end := text.find(delimiter, start)) >= 0:
            slash_count = len(text[:end]) - len(text[:end].rstrip("\\"))
            if delimiter == "'''" or slash_count % 2 == 0:
                return end + 3
            start = end + 1
        return -1

    def _paint(self, text: str, start: int, end: int, format: QTextCharFormat) -> None:
        # QTextLayout indexes UTF-16 units; Python regex indexes code points.
        qt_start = len(text[:start].encode("utf-16-le")) // 2
        qt_length = len(text[start:end].encode("utf-16-le")) // 2
        self.setFormat(qt_start, qt_length, format)

    def highlightBlock(self, text: str) -> None:
        self.setCurrentBlockState(0)
        for match in self._keys.finditer(text):
            self._paint(text, match.start(), match.end(), self.formats["key"])
        if re.match(r'^\s*\[', text):
            self._paint(text, 0, len(text), self.formats["header"])
        position = 0
        state = self.previousBlockState()
        if state in (1, 2):
            delimiter = '"""' if state == 1 else "'''"
            end = self._end(text, delimiter, 0)
            self._paint(text, 0, len(text) if end < 0 else end, self.formats["string"])
            if end < 0:
                self.setCurrentBlockState(state)
                return
            position = end
        while match := self._tokens.search(text, position):
            kind = match.lastgroup
            if kind == "triple":
                end = self._end(text, match[0], match.end())
                self._paint(text, match.start(), len(text) if end < 0 else end, self.formats["string"])
                if end < 0:
                    self.setCurrentBlockState(1 if match[0] == '"""' else 2)
                    return
                position = end
            else:
                self._paint(text, match.start(), match.end(), self.formats[kind])
                position = match.end()


class DiffHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.added = _format("#376B58", background="#EDF5E9")
        self.removed = _format("#A55648", background="#FAEDE8")
        self.header = _format("#66798A", bold=True)

    def highlightBlock(self, text: str) -> None:
        length = len(text.encode("utf-16-le")) // 2
        if text.startswith(("@@", "+++", "---")):
            self.setFormat(0, length, self.header)
        elif text.startswith("+"):
            self.setFormat(0, length, self.added)
        elif text.startswith("-"):
            self.setFormat(0, length, self.removed)
