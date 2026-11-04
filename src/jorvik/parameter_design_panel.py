"""Explicit metadata-only parameter designs and paginated configuration selection."""
from copy import deepcopy
import json
import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QCheckBox, QComboBox,
    QSpinBox, QDoubleSpinBox, QPushButton, QLabel, QTableWidget,
    QTableWidgetItem, QAbstractItemView, QMessageBox,
)


class ParameterDesignPanel(QWidget):
    changed = Signal()
    requestPreview = Signal()
    requestExport = Signal()
    PAGE_SIZE = 200
    COLUMNS = ('Name', 'Target', 'Mode', 'Start', 'Stop', 'Count',
               'Explicit values', 'Reference', 'Unit')

    def __init__(self, window, base_setup):
        super().__init__()
        self.window = window
        self.base_setup = base_setup
        self._building = False
        self._records = []
        self._fingerprint = None
        layout = QVBoxLayout(self)
        self.enabled_check = QCheckBox('Use a parameter design')
        self.enabled_check.setObjectName('design_enabled')
        layout.addWidget(self.enabled_check)
        explanation = QLabel('Bind named parameters to transform values. Generate configurations explicitly, '
                             'then select one to preview or export. Generating this table does not generate '
                             'spatial samples or process a sweep. Units describe the existing transform values; '
                             'no unit conversion is applied.')
        explanation.setWordWrap(True); layout.addWidget(explanation)
        self.editor = QWidget(); editor_layout = QVBoxLayout(self.editor)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        self.method = QComboBox()
        for label, key in [('Cartesian product', 'cartesian'), ('Paired values', 'paired'),
                           ('Seeded random', 'random'), ('Latin hypercube', 'lhs'), ('Sobol', 'sobol')]:
            self.method.addItem(label, key)
        self.method.setObjectName('design_method'); form.addRow('Design', self.method)
        self.seed = QDoubleSpinBox(); self.seed.setDecimals(0); self.seed.setRange(0, 4294967295)
        self.seed.setValue(42); self.seed.setObjectName('design_seed'); form.addRow('Design seed', self.seed)
        self.count = QSpinBox(); self.count.setRange(1, 10000); self.count.setValue(16)
        self.count.setObjectName('design_count'); form.addRow('Total random/LHS/Sobol configurations', self.count)
        editor_layout.addLayout(form)
        help_text = QLabel('Cartesian and paired designs use each parameter’s count or explicit list. '
                           'Paired lists must have equal lengths. Random/LHS/Sobol designs use ranges '
                           'and the total count above. The reference value is retained as metadata.')
        help_text.setWordWrap(True); editor_layout.addWidget(help_text)
        self.parameters = QTableWidget(0, len(self.COLUMNS))
        self.parameters.setHorizontalHeaderLabels(self.COLUMNS)
        self.parameters.setObjectName('design_parameters')
        self.parameters.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.parameters.setMinimumHeight(150)
        for column, width in enumerate((120, 160, 100, 90, 90, 70, 155, 100, 100)):
            self.parameters.setColumnWidth(column, width)
        editor_layout.addWidget(self.parameters)
        row = QHBoxLayout()
        self.add_button = QPushButton('Add parameter'); self.add_button.clicked.connect(self.add_parameter)
        self.remove_button = QPushButton('Remove selected parameter'); self.remove_button.clicked.connect(self.remove_parameter)
        row.addWidget(self.add_button); row.addWidget(self.remove_button); row.addStretch()
        editor_layout.addLayout(row)
        self.generate_button = QPushButton('Generate configurations')
        self.generate_button.setObjectName('generate_configurations')
        self.generate_button.clicked.connect(self.generate)
        editor_layout.addWidget(self.generate_button)
        layout.addWidget(self.editor)
        self.summary = QLabel(); self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.summary)
        self.configurations = QTableWidget(0, 1)
        self.configurations.setHorizontalHeaderLabels(['Configuration'])
        self.configurations.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.configurations.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.configurations.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.configurations.setObjectName('design_configurations')
        self.configurations.setMinimumHeight(150)
        layout.addWidget(self.configurations)
        pages = QHBoxLayout(); pages.addWidget(QLabel('Page'))
        self.page = QSpinBox(); self.page.setRange(1, 1); self.page.setObjectName('configuration_page')
        pages.addWidget(self.page); pages.addStretch()
        self.page_info = QLabel('No configurations generated.'); pages.addWidget(self.page_info)
        layout.addLayout(pages)
        actions = QHBoxLayout()
        self.preview_button = QPushButton('Preview selected configuration')
        self.export_button = QPushButton('Export selected configuration…')
        self.preview_button.clicked.connect(lambda: self.requestPreview.emit())
        self.export_button.clicked.connect(lambda: self.requestExport.emit())
        actions.addWidget(self.preview_button); actions.addWidget(self.export_button)
        layout.addLayout(actions)
        self.status = QLabel('Design disabled. Parameters defines one configuration.')
        self.status.setWordWrap(True); self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.parameters.itemChanged.connect(self._settings_changed)
        self.enabled_check.toggled.connect(self._settings_changed)
        self.method.currentIndexChanged.connect(self._settings_changed)
        self.seed.valueChanged.connect(self._settings_changed)
        self.count.valueChanged.connect(self._settings_changed)
        self.page.valueChanged.connect(self._render_page)
        self.configurations.currentCellChanged.connect(self._selection_changed)
        self._enabled_state()
        self.update_summary()

    @staticmethod
    def _number(text, name):
        try: value = float(text)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError(f'{name} must be a finite number.') from error
        if not math.isfinite(value): raise ValueError(f'{name} must be a finite number.')
        return value

    def _unit(self, target):
        if target.startswith('translation_'): return 'source'
        if target == 'rotation_angle': return self.base_setup().get('angle_units', 'degrees')
        return 'dimensionless'

    def _reference(self, target):
        setup = self.base_setup()
        if target.startswith('translation_'): return setup['translation']['xyz'.index(target[-1])]
        if target.startswith('scale_'): return setup['scale']['xyz'.index(target[-1])]
        if target == 'uniform_scale': return setup['scale'][0]
        return setup['rotation_angle']

    def _text(self, row, column):
        item = self.parameters.item(row, column)
        return item.text().strip() if item is not None else ''

    def design(self):
        if not self.enabled_check.isChecked(): return None
        parameters = []
        for row in range(self.parameters.rowCount()):
            target = self.parameters.cellWidget(row, 1).currentData()
            mode = self.parameters.cellWidget(row, 2).currentData()
            parameter = {'name': self._text(row, 0), 'target': target, 'mode': mode,
                         'reference': self._number(self._text(row, 7), 'Reference'),
                         'unit': self._unit(target)}
            if mode == 'range':
                parameter.update(start=self._number(self._text(row, 3), 'Start'),
                                 stop=self._number(self._text(row, 4), 'Stop'))
                if self.method.currentData() in ('cartesian', 'paired'):
                    try: parameter['count'] = int(self._text(row, 5))
                    except ValueError as error: raise ValueError('Parameter count must be an integer.') from error
            else:
                pieces = self._text(row, 6).split(',')
                parameter['values'] = [self._number(piece.strip(), 'Explicit value') for piece in pieces]
            parameters.append(parameter)
        return {'schema_version': 1, 'method': self.method.currentData(), 'seed': int(self.seed.value()),
                'count': self.count.value(), 'parameters': parameters}

    def _append_parameter(self, parameter):
        from .parameter_design import TARGETS
        row = self.parameters.rowCount(); self.parameters.insertRow(row)
        target = QComboBox()
        for key, label in TARGETS.items(): target.addItem(label, key)
        target.setCurrentIndex(target.findData(parameter['target']))
        mode = QComboBox(); mode.addItem('Range', 'range'); mode.addItem('Values', 'values')
        mode.setCurrentIndex(mode.findData(parameter['mode']))
        self.parameters.setCellWidget(row, 1, target); self.parameters.setCellWidget(row, 2, mode)
        fields = {0: parameter['name'], 3: repr(parameter.get('start', 0.)),
                  4: repr(parameter.get('stop', 1.)), 5: str(parameter.get('count', 2)),
                  6: ', '.join(repr(value) for value in parameter.get('values', [0., 1.])),
                  7: repr(parameter['reference']), 8: self._unit(parameter['target'])}
        for column, text in fields.items():
            item = QTableWidgetItem(str(text))
            if column == 8: item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.parameters.setItem(row, column, item)
        target.currentIndexChanged.connect(self._target_changed)
        mode.currentIndexChanged.connect(self._settings_changed)

    def add_parameter(self, *_):
        from .parameter_design import TARGETS
        if self.parameters.rowCount() >= 8: return
        used = {self.parameters.cellWidget(row, 1).currentData() for row in range(self.parameters.rowCount())}
        available = [key for key in TARGETS if key not in used
                     and not (key == 'uniform_scale' and used & {'scale_x', 'scale_y', 'scale_z'})
                     and not (key.startswith('scale_') and 'uniform_scale' in used)]
        if not available: return
        target = available[0]
        reference = float(self._reference(target))
        low, high = (1., 2.) if 'scale' in target else (0., 1.)
        names = {self._text(row, 0) for row in range(self.parameters.rowCount())}
        name = target; suffix = 1
        while name in names: name = f'{target}_{suffix}'; suffix += 1
        self._building = True
        try:
            self._append_parameter({'name': name, 'target': target, 'mode': 'range',
                                    'start': low, 'stop': high, 'count': 2,
                                    'reference': reference, 'unit': self._unit(target)})
        finally: self._building = False
        self._settings_changed()

    def remove_parameter(self, *_):
        row = self.parameters.currentRow()
        if row < 0: return
        self._building = True
        try: self.parameters.removeRow(row)
        finally: self._building = False
        self._settings_changed()

    def _target_changed(self, *_):
        if self._building: return
        self._building = True
        try:
            for row in range(self.parameters.rowCount()):
                self.parameters.item(row, 8).setText(self._unit(self.parameters.cellWidget(row, 1).currentData()))
        finally: self._building = False
        self._settings_changed()

    def _enabled_state(self):
        active = self.enabled_check.isChecked()
        self.editor.setEnabled(active)
        stochastic = self.method.currentData() in ('random', 'lhs', 'sobol')
        self.count.setEnabled(active and stochastic)
        self.seed.setEnabled(active and stochastic)
        ready = active and bool(self._records)
        self.preview_button.setEnabled(ready); self.export_button.setEnabled(ready)
        self.page.setEnabled(ready and self.page.maximum() > 1)

    def _settings_changed(self, *_):
        if self._building: return
        self.invalidate()
        self._enabled_state(); self.update_summary()
        self.changed.emit()

    def invalidate(self):
        self._records = []; self._fingerprint = None
        self.configurations.setRowCount(0)
        self.page_info.setText('No current configuration table.')
        self.preview_button.setEnabled(False); self.export_button.setEnabled(False); self.page.setEnabled(False)
        self.status.setText('Configurations are stale. Generate configurations again.' if self.enabled_check.isChecked()
                            else 'Design disabled. Parameters defines one configuration.')

    def update_summary(self):
        if not self.enabled_check.isChecked():
            self.summary.setText('One configuration from Parameters. Enable a design to compare multiple transform values.')
            return
        try:
            from .parameter_design import validate_design, design_summary
            design = self.design(); validate_design(design, self.base_setup())
            summary = design_summary(design, self.window.sampling.batches)
            regions = ', '.join(f"{record['label']}: {record['points']}" for record in summary['per_region'])
            warnings = '\n'.join(summary.get('warnings', []))
            self.summary.setText(f"{summary['count']:,} configurations × {summary['points_per_configuration']:,} points each = "
                                 f"{summary['total_points']:,} points. Estimated numeric output: "
                                 f"{summary['estimated_bytes'] / (1024**2):,.2f} MiB.\n"
                                 f"Points per region/configuration: {regions or 'No generated samples.'}"
                                 + ('\n' + warnings if warnings else ''))
        except (ValueError, TypeError, KeyError) as error:
            self.summary.setText('Design needs attention: ' + str(error))

    def apply_design(self, design):
        """Validate before replacing controls; never generate or preview on load."""
        if design is not None:
            from .parameter_design import validate_design, design_summary
            validate_design(design, self.base_setup())
            design_summary(design)
            # The controls use integer-safe ranges; preserve all floating values in text.
            if (not 0 <= design['seed'] <= 4294967295 or type(design.get('count', 16)) is not int
                    or not 1 <= design.get('count', 16) <= 10000):
                raise ValueError('Design seed/count exceeds the workspace range.')
            data = deepcopy(design)
        else:
            data = {'method': 'cartesian', 'seed': 42, 'count': 16, 'parameters': []}
        self._building = True
        try:
            self.enabled_check.setChecked(design is not None)
            self.method.setCurrentIndex(self.method.findData(data['method']))
            self.seed.setValue(data['seed']); self.count.setValue(data.get('count', 16))
            self.parameters.setRowCount(0)
            for parameter in data['parameters']: self._append_parameter(parameter)
        finally: self._building = False
        self.invalidate(); self._enabled_state(); self.update_summary(); self.changed.emit()

    @staticmethod
    def _key(base, design):
        return json.dumps({'base': base, 'design': design}, sort_keys=True, allow_nan=False)

    def generate(self, *_):
        if not self.enabled_check.isChecked(): return
        try:
            from .parameter_design import generate_configurations
            base = self.base_setup(); design = self.design()
            records = generate_configurations(base, design)
            key = self._key(base, design)
        except (ValueError, TypeError, KeyError) as error:
            QMessageBox.warning(self, 'Parameter design', str(error)); return
        self._records = records; self._fingerprint = key
        self.page.blockSignals(True)
        self.page.setRange(1, max(1, math.ceil(len(records) / self.PAGE_SIZE))); self.page.setValue(1)
        self.page.blockSignals(False)
        self._render_page()
        self._enabled_state(); self.update_summary()
        self.status.setText(f'Generated {len(records):,} configurations. Select one, then preview or export explicitly.')

    def _render_page(self, *_):
        names = list(self._records[0]['parameters']) if self._records else []
        self.configurations.setColumnCount(1 + len(names))
        self.configurations.setHorizontalHeaderLabels(['Configuration', *names])
        start = (self.page.value() - 1) * self.PAGE_SIZE
        rows = self._records[start:start + self.PAGE_SIZE]
        self.configurations.setRowCount(len(rows))
        for row, record in enumerate(rows):
            values = [record['id'], *[format(record['parameters'][name], '.10g') for name in names]]
            for column, value in enumerate(values): self.configurations.setItem(row, column, QTableWidgetItem(value))
        self.configurations.resizeColumnsToContents()
        if rows: self.configurations.setCurrentCell(0, 0)
        self.page_info.setText(f'{start + 1 if rows else 0}–{start + len(rows)} of {len(self._records)} configurations')

    def _selection_changed(self, *_):
        if not self._records: return
        index = (self.page.value() - 1) * self.PAGE_SIZE + self.configurations.currentRow()
        if 0 <= index < len(self._records):
            self.status.setText('Selected ' + self._records[index]['id'] + '. Preview or export explicitly.')

    def selected_setup(self, base_setup):
        if not self.enabled_check.isChecked(): return deepcopy(base_setup)
        design = self.design()
        if not self._records or self._key(base_setup, design) != self._fingerprint:
            raise ValueError('Configurations are stale. Generate configurations again before preview or export.')
        row = self.configurations.currentRow()
        if row < 0: raise ValueError('Select a configuration first.')
        index = (self.page.value() - 1) * self.PAGE_SIZE + row
        if not 0 <= index < len(self._records): raise ValueError('Select a configuration first.')
        return deepcopy(self._records[index]['setup'])
