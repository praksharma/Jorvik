"""Literal comparison editor and bounded read-only rejection report."""
import math
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QComboBox, QDialog, QLineEdit, QSpinBox, QPlainTextEdit)
from .parameter_design import COMPARISONS, MAX_CONSTRAINTS


class ConstraintEditor(QWidget):
    changed = Signal()

    def __init__(self, names):
        super().__init__()
        self.names = names; self._building = False
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0)
        note = QLabel('Optional constraints: keep rows only when every rule passes. '
                      'Use exact parameter names. Constants use the left parameter’s units. No expressions or geometry checks.')
        note.setWordWrap(True); layout.addWidget(note)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['Rule name', 'Left parameter', 'Comparison', 'Right kind', 'Right value / name'])
        self.table.setMaximumHeight(145); layout.addWidget(self.table)
        buttons = QHBoxLayout()
        self.add_button = QPushButton('Add constraint'); self.add_button.clicked.connect(self.add_rule)
        self.remove_button = QPushButton('Remove selected constraint'); self.remove_button.clicked.connect(self.remove_rule)
        buttons.addWidget(self.add_button); buttons.addWidget(self.remove_button); buttons.addStretch(); layout.addLayout(buttons)
        self.table.itemChanged.connect(self.emit_changed)
        for column, width in enumerate((125, 150, 110, 110, 180)): self.table.setColumnWidth(column, width)

    def emit_changed(self, *_):
        if not self._building: self.changed.emit()

    def append(self, rule):
        row = self.table.rowCount(); self.table.insertRow(row)
        comparison = QComboBox(); comparison.addItems(list(COMPARISONS))
        comparison.setCurrentText(rule['operator']); self.table.setCellWidget(row, 2, comparison)
        kind = QComboBox(); kind.addItem('Constant', 'value'); kind.addItem('Parameter', 'parameter')
        key = next(iter(rule['right'])); kind.setCurrentIndex(kind.findData(key)); self.table.setCellWidget(row, 3, kind)
        for column, value in ((0, rule['name']), (1, rule['left']), (4, rule['right'][key])):
            self.table.setItem(row, column, QTableWidgetItem(str(value)))
        comparison.currentIndexChanged.connect(self.emit_changed); kind.currentIndexChanged.connect(self.emit_changed)

    def add_rule(self, *_):
        if self.table.rowCount() >= MAX_CONSTRAINTS: return
        names = self.names()
        existing = {self.table.item(row, 0).text() for row in range(self.table.rowCount())}
        index = 1
        while f'rule_{index}' in existing: index += 1
        self._building = True
        try: self.append({'name': f'rule_{index}', 'left': names[0] if names else '', 'operator': '<=', 'right': {'value': 0.}})
        finally: self._building = False
        self.changed.emit()

    def remove_rule(self, *_):
        row = self.table.currentRow()
        if row >= 0:
            self._building = True
            try: self.table.removeRow(row)
            finally: self._building = False
            self.changed.emit()

    def rules(self):
        rules = []
        for row in range(self.table.rowCount()):
            kind = self.table.cellWidget(row, 3).currentData()
            value = self.table.item(row, 4).text().strip()
            if kind == 'value':
                try: value = float(value)
                except (ValueError, OverflowError): raise ValueError('Constraint constants must be finite numbers.') from None
                if not math.isfinite(value): raise ValueError('Constraint constants must be finite numbers.')
            rules.append({'name': self.table.item(row, 0).text().strip(),
                          'left': self.table.item(row, 1).text().strip(),
                          'operator': self.table.cellWidget(row, 2).currentText(), 'right': {kind: value}})
        return rules

    def apply_rules(self, rules):
        self._building = True
        try:
            self.table.setRowCount(0)
            for rule in rules: self.append(rule)
        finally: self._building = False


class RejectedConfigurationsDialog(QDialog):
    def __init__(self, records, parent):
        super().__init__(parent)
        self.records = records
        self.setWindowTitle('Rejected configurations'); self.resize(850, 600)
        layout = QVBoxLayout(self)
        note = QLabel(f'{len(records):,} rejected candidates. Original IDs and values are retained; no replacement draws.')
        note.setWordWrap(True); layout.addWidget(note)
        self.search = QLineEdit(); self.search.setPlaceholderText('Search IDs, parameter values or rejection reasons')
        layout.addWidget(self.search)
        self.text = QPlainTextEdit(); self.text.setReadOnly(True); layout.addWidget(self.text)
        pages = QHBoxLayout(); pages.addWidget(QLabel('Page')); self.page = QSpinBox(); pages.addWidget(self.page)
        self.info = QLabel(); pages.addWidget(self.info); pages.addStretch(); layout.addLayout(pages)
        close = QPushButton('Close'); close.clicked.connect(self.accept); layout.addWidget(close)
        self.search.textChanged.connect(self.filter); self.page.valueChanged.connect(self.render)
        self.filtered = []; self.filter()

    def filter(self, *_):
        query = self.search.text().casefold()
        self.filtered = [self.format(record) for record in self.records if not query or query in self.format(record).casefold()]
        self.page.blockSignals(True); self.page.setRange(1, max(1, (len(self.filtered)+199)//200)); self.page.setValue(1)
        self.page.blockSignals(False); self.render()

    @staticmethod
    def format(record):
        values = ', '.join(f'{key}={value!r}' for key, value in record['parameters'].items())
        return record['id'] + ' · ' + values + '\n  ' + '\n  '.join(record['reasons'])

    def render(self, *_):
        start = (self.page.value()-1)*200
        self.text.setPlainText('\n\n'.join(self.filtered[start:start+200]))
        self.info.setText(f'{len(self.filtered):,} matches / {len(self.records):,} rejected')
