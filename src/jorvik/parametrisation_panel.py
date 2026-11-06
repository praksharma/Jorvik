"""Explicit single-configuration processing workspace; no automatic computation."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QTabWidget, QWidget, QFormLayout,
    QComboBox, QDoubleSpinBox, QListWidget, QListWidgetItem, QLabel, QPushButton,
    QHBoxLayout, QFileDialog, QMessageBox, QScrollArea)
from pathlib import Path
import math
from copy import deepcopy
from .parameter_design_panel import ParameterDesignPanel
from .sweep_panel import SweepExportPanel
from .parametrisation import export_configuration, validate_setup, save_setup, load_setup


class ParametrisationDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle('Parametrisation — Transform configuration')
        self.resize(660, 650)
        self.setModal(False)
        self._pending_finish = None
        self.source_hash = window.geometry['source_hash'] if window.geometry else None
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        parameters = QWidget(); form = QFormLayout(parameters)
        self.scope = QComboBox()
        self.scope.addItem('Whole domain — consistent transformation', 'domain')
        self.scope.addItem('Selected regions — dataset augmentation', 'regions')
        form.addRow('Scope', self.scope)
        def vector_row(label, name, defaults, low, high):
            row = QHBoxLayout(); spins = []
            for axis, value in zip('XYZ', defaults):
                spin = QDoubleSpinBox(); spin.setDecimals(8); spin.setRange(low, high)
                spin.setFixedWidth(120)
                spin.setValue(value); spin.setObjectName(f'{name}_{axis.lower()}')
                spin.setAccessibleName(f'{label} {axis}')
                axis_label = QLabel(axis); axis_label.setFixedWidth(12)
                row.addWidget(axis_label); row.addWidget(spin); spins.append(spin)
            row.addStretch()
            form.addRow(label, row)
            return spins
        self.offsets = vector_row('Translation', 'translation', [0, 0, 0], -1e12, 1e12)
        self.rotation_axis = vector_row('Rotation axis', 'rotation_axis', [0, 0, 1], -1e6, 1e6)
        self.rotation_angle = QDoubleSpinBox(); self.rotation_angle.setDecimals(8)
        self.rotation_angle.setRange(-360000, 360000); self.rotation_angle.setSuffix(' °')
        self.rotation_angle.setObjectName('rotation_angle'); self.rotation_angle.setAccessibleName('Rotation angle in degrees')
        form.addRow('Rotation angle', self.rotation_angle)
        self.scales = vector_row('Scale factors', 'scale', [1, 1, 1], 1e-8, 1e8)
        self.pivots = vector_row('Pivot', 'pivot', [0, 0, 0], -1e12, 1e12)
        instructions = QLabel('Order: scale → rotate → translate in world coordinates. '
                              'Scale and rotation share the pivot. Distances use source geometry units; '
                              'the rotation axis must be nonzero. Equal scale factors give uniform scaling.')
        instructions.setWordWrap(True); form.addRow(instructions)
        density = QLabel('Unequal scale factors can change point density on the transformed boundary. '
                         'Exports do not add integration weights. Planar edge normals are omitted after '
                         'unequal scaling when their direction cannot be verified; exports record the reason.')
        density.setWordWrap(True); form.addRow(density)
        self.regions = QListWidget()
        for batch in window.sampling.batches:
            item = QListWidgetItem(batch['label'], self.regions)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if batch['label'] == window.selected_area else Qt.CheckState.Unchecked)
        self.regions.setMaximumHeight(140)
        form.addRow('Affected regions', self.regions)
        self.warning = QLabel(''); self.warning.setWordWrap(True); form.addRow(self.warning)
        self.scope.currentIndexChanged.connect(self.scope_changed)
        self.scope_changed()
        parameter_scroll = QScrollArea(); parameter_scroll.setWidgetResizable(True)
        parameter_scroll.setWidget(parameters)
        self.tabs.addTab(parameter_scroll, 'Parameters')
        self.design_panel = ParameterDesignPanel(window, self._base_setup)
        combinations = QScrollArea(); combinations.setWidgetResizable(True)
        combinations.setWidget(self.design_panel)
        self.tabs.addTab(combinations, 'Combinations')
        self.design_panel.requestPreview.connect(self.preview)
        self.design_panel.requestExport.connect(self.export_configuration)
        processing = QWidget(); box = QVBoxLayout(processing)
        text = QLabel('Preview explicitly applies one configuration to the viewport. Original geometry and sampled datasets stay unchanged. Restore or close this workspace to return to the originals.')
        text.setWordWrap(True); box.addWidget(text)
        self.preview_button = QPushButton('Preview configuration'); self.preview_button.clicked.connect(self.preview)
        box.addWidget(self.preview_button)
        self.restore_button = QPushButton('Restore original view'); self.restore_button.clicked.connect(self.restore); box.addWidget(self.restore_button)
        self.sweep_panel = SweepExportPanel(window, self.sweep_snapshot)
        self.sweep_panel.busyChanged.connect(self.sweep_busy_changed)
        box.addWidget(self.sweep_panel)
        self.status = QLabel('Not processed.'); self.status.setWordWrap(True); box.addStretch()
        processing_scroll = QScrollArea(); processing_scroll.setWidgetResizable(True)
        processing_scroll.setWidget(processing)
        self.tabs.addTab(processing_scroll, 'Processing')
        export = QWidget(); box = QVBoxLayout(export)
        text = QLabel('Process the current configuration from the original samples and export every region to NetCDF or NPZ. Parameter values, scope and original sampling metadata are retained. Hidden regions are included.')
        text.setWordWrap(True); box.addWidget(text)
        self.export_button = QPushButton('Process configuration and export…')
        self.export_button.clicked.connect(self.export_configuration); box.addWidget(self.export_button); box.addStretch()
        self.tabs.addTab(export, 'Export')
        layout.addWidget(self.status)
        for spin in [*self.offsets, *self.rotation_axis, self.rotation_angle, *self.scales, *self.pivots]:
            spin.valueChanged.connect(self.mark_changed)
        self.design_panel.changed.connect(self.mark_changed)
        self.scope.currentIndexChanged.connect(self.mark_changed)
        self.regions.itemChanged.connect(self.mark_changed)
        self.reset_button = QPushButton('Reset setup')
        self.reset_button.clicked.connect(self.reset_setup)
        layout.addWidget(self.reset_button)
        self.setup_buttons = []
        buttons = QHBoxLayout(); layout.addLayout(buttons)
        for title, callback in [('Save setup…', self.save), ('Load setup…', self.load), ('Close', self.close)]:
            button = QPushButton(title); button.clicked.connect(callback); buttons.addWidget(button)
            if title != 'Close': self.setup_buttons.append(button)
        if window.parametrisation_setup:
            self.apply_setup(window.parametrisation_setup)

    def apply_setup(self, setup):
        """Validate the complete configuration before restoring or changing controls."""
        validate_setup(setup)
        advanced = setup if setup['schema_version'] == 2 else {}
        values = [
            (self.offsets, setup['translation']),
            (self.rotation_axis, advanced.get('rotation_axis', [0, 0, 1])),
            (self.scales, advanced.get('scale', [1, 1, 1])),
            (self.pivots, advanced.get('pivot', [0, 0, 0])),
        ]
        angle = advanced.get('rotation_angle', 0.)
        if advanced.get('angle_units', 'degrees') == 'radians':
            angle = math.degrees(angle)
        assignments = [(spin, value) for spins, vector in values for spin, value in zip(spins, vector)]
        assignments.append((self.rotation_angle, angle))
        for spin, value in assignments:
            if (not math.isfinite(value) or not spin.minimum() <= value <= spin.maximum()
                    or abs(value - round(value, spin.decimals())) > max(1e-14, abs(value) * 1e-14)):
                raise ValueError('A transformation value exceeds the range or precision supported by this workspace.')
        design = deepcopy(setup.get('design'))
        if design is not None and advanced.get('angle_units') == 'radians':
            # This workspace displays degrees. Convert the bound angle definition
            # with the base angle rather than silently relabelling radian values.
            for parameter in design['parameters']:
                if parameter['target'] == 'rotation_angle':
                    for field in ('start', 'stop', 'reference'):
                        if field in parameter: parameter[field] = math.degrees(parameter[field])
                    if 'values' in parameter:
                        parameter['values'] = [math.degrees(value) for value in parameter['values']]
                    parameter['unit'] = 'degrees'
            angle_names = {parameter['name'] for parameter in design['parameters'] if parameter['target'] == 'rotation_angle'}
            for rule in design.get('constraints', []):
                if rule['left'] in angle_names and 'value' in rule['right']:
                    rule['right']['value'] = math.degrees(rule['right']['value'])
        self.design_panel.apply_design(design)
        self.restore()
        self.scope.setCurrentIndex(self.scope.findData(setup['scope']))
        for spin, value in assignments: spin.setValue(value)
        for i in range(self.regions.count()):
            item = self.regions.item(i)
            item.setCheckState(Qt.CheckState.Checked if item.text() in setup['regions'] else Qt.CheckState.Unchecked)
        self.source_hash = setup['source_hash']


    def mark_changed(self):
        self.design_panel.invalidate()
        self.design_panel.update_summary()
        if self.window.view.translation_preview is not None:
            self.status.setText('Parameters changed. The viewport still shows the previous preview; click Preview configuration to update.')
        else:
            self.status.setText('Parameters changed. Preview or process/export explicitly.')

    def reset_setup(self):
        """Start an identity configuration for this model and restore its view."""
        self.restore()
        self.source_hash = self.window.geometry['source_hash'] if self.window.geometry else None
        self.scope.setCurrentIndex(self.scope.findData('domain'))
        for spin in [*self.offsets, *self.pivots]: spin.setValue(0)
        for spin, value in zip(self.rotation_axis, [0, 0, 1]): spin.setValue(value)
        self.rotation_angle.setValue(0)
        for spin in self.scales: spin.setValue(1)
        for i in range(self.regions.count()):
            self.regions.item(i).setCheckState(Qt.CheckState.Unchecked)
        self.design_panel.apply_design(None)
        self.status.setText('Setup reset to the whole-domain identity transformation. Preview or process/export explicitly.')

    def scope_changed(self):
        regions = self.scope.currentData() == 'regions'
        self.regions.setEnabled(regions)
        self.warning.setText('Only checked point clouds move. CAD geometry stays fixed; boundary/interior consistency is unverified.' if regions else 'Geometry display and all sampled regions transform together. The source CAD file is unchanged.')

    def _base_setup(self):
        return {'schema_version': 2, 'scope': self.scope.currentData(),
                'translation': [spin.value() for spin in self.offsets],
                'rotation_axis': [spin.value() for spin in self.rotation_axis],
                'rotation_angle': self.rotation_angle.value(), 'angle_units': 'degrees',
                'scale': [spin.value() for spin in self.scales],
                'pivot': [spin.value() for spin in self.pivots],
                'order': ['scale', 'rotate', 'translate'], 'frame': 'world',
                'regions': [self.regions.item(i).text() for i in range(self.regions.count())
                            if self.regions.item(i).checkState() == Qt.CheckState.Checked],
                'source_hash': self.source_hash}

    def setup(self):
        setup = self._base_setup()
        design = self.design_panel.design()
        if design is not None: setup['design'] = design
        return setup

    def active_setup(self):
        setup = self.design_panel.selected_setup(self._base_setup())
        design = self.design_panel.design()
        if design is not None:
            # Only the chosen row carries the full definition into exports.
            # The generated table holds scalar setups and stays bounded in size.
            setup['design'] = design
        validate_setup(setup, self.window.sampling.batches)
        return setup

    def sweep_snapshot(self):
        base = self.setup()
        validate_setup(base, self.window.sampling.batches)
        records = self.design_panel.generated_records(self._base_setup())
        return base, records

    def sweep_busy_changed(self, busy):
        for index in (0, 1, 3): self.tabs.widget(index).setEnabled(not busy)
        for widget in [self.preview_button, self.restore_button, self.reset_button, *self.setup_buttons]:
            widget.setEnabled(not busy)
        if not busy and self._pending_finish is not None:
            result, self._pending_finish = self._pending_finish, None
            QTimer.singleShot(0, lambda: self.done(result))

    def defer_finish(self, result):
        if self.sweep_panel.running:
            self._pending_finish = result
            self.sweep_panel.cancel()
            self.status.setText('Finishing the current output, then closing the workspace…')
            return True
        return False

    def report_error(self, error):
        QMessageBox.warning(self, 'Parametrisation', str(error))

    def preview(self):
        if self.sweep_panel.running: return
        try:
            setup = self.active_setup()
            self.window.view.set_translation_preview(setup)
            self.status.setText('Preview active — original datasets unchanged. Export processes the selected values independently.')
        except (ValueError, TypeError) as error:
            self.report_error(error)

    def restore(self):
        self.window.view.set_translation_preview(None)
        self.status.setText('Original view restored.')

    def done(self, result):
        if self.defer_finish(result): return
        self.restore()
        super().done(result)

    def closeEvent(self, event):
        if self.defer_finish(0):
            event.ignore()
            return
        self.restore()
        super().closeEvent(event)

    def export_configuration(self):
        if self.sweep_panel.running: return
        try:
            setup = self.active_setup()
            filename, selected = QFileDialog.getSaveFileName(self, 'Export transformed configuration', 'transformed_samples.nc', 'NetCDF (*.nc);;NumPy archive (*.npz)')
            if not filename:
                return
            path = Path(filename)
            if not path.suffix:
                path = path.with_suffix('.npz' if 'npz' in selected else '.nc')
            export_configuration(self.window.sampling.batches, setup, path)
            self.status.setText(f'Processed 1 configuration. Exported to {path}')
        except (ValueError, TypeError, OSError) as error:
            self.report_error(error)

    def save(self):
        filename, _ = QFileDialog.getSaveFileName(self, 'Save parametrisation setup', 'parametrisation.json', 'JSON (*.json)')
        if filename:
            try:
                path = Path(filename)
                save_setup(self.setup(), path if path.suffix else path.with_suffix('.json'))
            except (ValueError, TypeError, OSError) as error:
                self.report_error(error)

    def load(self):
        filename, _ = QFileDialog.getOpenFileName(self, 'Load parametrisation setup', '', 'JSON (*.json)')
        if filename:
            try:
                setup = load_setup(filename)
                validate_setup(setup, self.window.sampling.batches)
                self.apply_setup(setup)
                self.status.setText('Setup loaded. Preview or process explicitly.')
            except (ValueError, TypeError, OSError) as error:
                self.report_error(error)
