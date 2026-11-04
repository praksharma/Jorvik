"""Explicit creation and editing of prepared boundary assignments."""
from .history import undoable
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QListWidget,
    QListWidgetItem, QLineEdit, QComboBox, QPushButton, QHBoxLayout, QLabel,
    QMessageBox, QColorDialog, QDialogButtonBox)
from .regions import put_region


class BoundaryRegionsDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.identity = None
        self.colour = '#41c4b8'
        self.setWindowTitle('Boundary regions')
        self.resize(540, 650)
        layout = QVBoxLayout(self)
        note = QLabel('Assign labels to existing faces or edges. Overlaps are allowed; each region is sampled independently. Dataset labels are separate.')
        note.setWordWrap(True); layout.addWidget(note)
        self.regions = QListWidget(); layout.addWidget(self.regions)
        self.regions.currentItemChanged.connect(self.load_region)
        self.new_button = QPushButton('New from current selection')
        self.new_button.clicked.connect(self.new_region); layout.addWidget(self.new_button)
        form = QFormLayout(); layout.addLayout(form)
        self.label = QLineEdit(); form.addRow('Region label', self.label)
        self.kind = QComboBox(); self.kind.addItem('Faces', 2); self.kind.addItem('Edges', 1)
        form.addRow('Entities', self.kind)
        self.colour_button = QPushButton(); self.colour_button.clicked.connect(self.choose_colour)
        form.addRow('Colour', self.colour_button)
        self.entities = QListWidget(); layout.addWidget(self.entities)
        self.kind.currentIndexChanged.connect(self.populate_entities)
        self.details = QLabel(); self.details.setWordWrap(True)
        self.details.setTextFormat(Qt.TextFormat.PlainText); layout.addWidget(self.details)
        buttons = QHBoxLayout(); layout.addLayout(buttons)
        self.save_button = QPushButton('Save region'); self.save_button.clicked.connect(self.save_region)
        self.delete_button = QPushButton('Delete region'); self.delete_button.clicked.connect(self.delete_region)
        self.select_button = QPushButton('Select entities'); self.select_button.clicked.connect(self.select_entities)
        for button in (self.save_button, self.delete_button, self.select_button): buttons.addWidget(button)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject); layout.addWidget(close)
        self.refresh_list()
        self.new_region()

    def refresh_list(self, selected=None):
        self.regions.blockSignals(True); self.regions.clear()
        for region in self.window.boundary_regions:
            item = QListWidgetItem(region['label'], self.regions)
            item.setData(Qt.ItemDataRole.UserRole, region['id'])
            item.setForeground(QColor(region['colour']))
            item.setToolTip(', '.join(f"{d}:{t}" for d, t in region['entities']))
            if region['id'] == selected: self.regions.setCurrentItem(item)
        self.regions.blockSignals(False)

    def populate_entities(self, *_):
        self.entities.clear()
        dimension = self.kind.currentData()
        for entity in self.window.geometry['entities']:
            if entity['dimension'] != dimension: continue
            item = QListWidgetItem(f"{'Face' if dimension == 2 else 'Edge'} {entity['tag']} · {entity['type']}", self.entities)
            item.setData(Qt.ItemDataRole.UserRole, [dimension, entity['tag']])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)

    def set_members(self, members):
        self.kind.setCurrentIndex(self.kind.findData(members[0][0] if members else 2))
        self.populate_entities()
        for i in range(self.entities.count()):
            item = self.entities.item(i)
            item.setCheckState(Qt.CheckState.Checked if item.data(Qt.ItemDataRole.UserRole) in members else Qt.CheckState.Unchecked)

    def update_colour(self):
        self.colour_button.setText(self.colour)
        self.colour_button.setStyleSheet(f'border: 2px solid {self.colour}; padding: 4px;')

    def choose_colour(self):
        colour = QColorDialog.getColor(QColor(self.colour), self, 'Boundary region colour')
        if colour.isValid():
            self.colour = colour.name(); self.update_colour()

    def new_region(self):
        self.identity = None
        self.regions.setCurrentItem(None)
        self.regions.clearSelection()
        self.label.clear()
        self.colour = '#41c4b8'; self.update_colour()
        self.set_members([list(key) for key in sorted(self.window.selection)])
        self.delete_button.setEnabled(False); self.select_button.setEnabled(False)
        self.details.setText('New region. Choose members and click Save region. Unlabelled entities remain available.')

    def load_region(self, item, previous=None):
        if item is None: return
        region = next(r for r in self.window.boundary_regions if r['id'] == item.data(Qt.ItemDataRole.UserRole))
        self.identity = region['id']; self.label.setText(region['label'])
        self.colour = region['colour']; self.update_colour(); self.set_members(region['entities'])
        self.delete_button.setEnabled(True); self.select_button.setEnabled(True)
        self.details.setText(f"{len(region['entities'])} members: " + ', '.join(f'{d}:{t}' for d, t in region['entities']) + '\nOverlaps use the first-created region colour in the viewport; selection highlights every member.')

    @undoable('save boundary region')
    def save_region(self):
        members = [self.entities.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.entities.count())
                   if self.entities.item(i).checkState() == Qt.CheckState.Checked]
        try:
            regions = put_region(self.window.boundary_regions, self.window.geometry,
                                 self.label.text(), members, self.colour, self.identity)
            self.identity = self.identity or regions[-1]['id']
            self.window.boundary_regions = regions
            self.window.refresh_boundary_regions()
            self.refresh_list(self.identity)
            self.load_region(self.regions.currentItem())
        except (ValueError, TypeError) as error:
            QMessageBox.warning(self, 'Boundary region', str(error))

    @undoable('delete boundary region')
    def delete_region(self):
        if self.identity is None: return
        self.window.boundary_regions = [r for r in self.window.boundary_regions if r['id'] != self.identity]
        self.window.refresh_boundary_regions()
        self.refresh_list(); self.new_region()

    def select_entities(self):
        region = next(r for r in self.window.boundary_regions if r['id'] == self.identity)
        self.window.set_selection_mode('face' if region['entities'][0][0] == 2 else 'edge')
        self.window.selection = {tuple(e) for e in region['entities']}
        self.window.update_selection()
