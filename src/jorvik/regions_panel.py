"""Explicit creation and editing of prepared boundary assignments."""
from .history import undoable
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QListWidget,
    QListWidgetItem, QLineEdit, QComboBox, QPushButton, QHBoxLayout, QLabel,
    QMessageBox, QColorDialog, QDialogButtonBox)
from .regions import put_region

AUTO_PALETTE = ('#41c4b8', '#f0a830', '#5c9ce6', '#e06c75', '#98c379', '#c678dd', '#e5c07b', '#56b6c2')


class BoundaryRegionsDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.identity = None
        self.colour = '#41c4b8'
        self.setWindowTitle('Boundary regions')
        self.resize(560, 700)
        layout = QVBoxLayout(self)
        note = QLabel('Assign labels to existing faces or edges. Overlaps are allowed; each region is sampled independently. Dataset labels are separate.')
        note.setWordWrap(True); layout.addWidget(note)
        self.regions = QListWidget(); layout.addWidget(self.regions)
        self.regions.currentItemChanged.connect(self.load_region)
        top_buttons = QHBoxLayout()
        self.new_button = QPushButton('New from current selection')
        self.new_button.clicked.connect(self.new_region)
        self.auto_create_button = QPushButton('Auto-create from groups / CAD names')
        self.auto_create_button.setToolTip('Automatically create boundary regions from imported 1D/2D physical groups and named CAD faces/edges.')
        self.auto_create_button.clicked.connect(self.auto_create_regions)
        top_buttons.addWidget(self.new_button)
        top_buttons.addWidget(self.auto_create_button)
        layout.addLayout(top_buttons)
        form = QFormLayout(); layout.addLayout(form)
        self.label = QLineEdit(); form.addRow('Region label', self.label)
        self.kind = QComboBox(); self.kind.addItem('Faces', 2); self.kind.addItem('Edges', 1)
        form.addRow('Entities', self.kind)
        self.colour_button = QPushButton(); self.colour_button.clicked.connect(self.choose_colour)
        form.addRow('Colour', self.colour_button)
        filter_row = QHBoxLayout()
        self.entity_filter = QLineEdit()
        self.entity_filter.setPlaceholderText('Filter entities by tag, type or CAD name…')
        self.entity_filter.setClearButtonEnabled(True)
        self.entity_filter.textChanged.connect(self.filter_entities)
        self.check_visible_button = QPushButton('Check visible')
        self.check_visible_button.clicked.connect(self.check_visible_entities)
        self.uncheck_all_button = QPushButton('Uncheck all')
        self.uncheck_all_button.clicked.connect(self.uncheck_all_entities)
        filter_row.addWidget(self.entity_filter, 1)
        filter_row.addWidget(self.check_visible_button)
        filter_row.addWidget(self.uncheck_all_button)
        layout.addLayout(filter_row)
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
            title = f"{'Face' if dimension == 2 else 'Edge'} {entity['tag']} · {entity['type']}"
            if entity.get('name'):
                title += f" ({entity['name']})"
            item = QListWidgetItem(title, self.entities)
            item.setData(Qt.ItemDataRole.UserRole, [dimension, entity['tag']])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
        self.filter_entities(self.entity_filter.text())

    def filter_entities(self, query=''):
        needle = str(query).strip().lower()
        for i in range(self.entities.count()):
            item = self.entities.item(i)
            item.setHidden(bool(needle and needle not in item.text().lower()))

    def check_visible_entities(self):
        for i in range(self.entities.count()):
            item = self.entities.item(i)
            if not item.isHidden():
                item.setCheckState(Qt.CheckState.Checked)

    def uncheck_all_entities(self):
        for i in range(self.entities.count()):
            self.entities.item(i).setCheckState(Qt.CheckState.Unchecked)

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

    @undoable('auto-create boundary regions')
    def auto_create_regions(self):
        geometry = self.window.geometry
        if not geometry:
            return
        regions = list(self.window.boundary_regions)
        used_labels = {r['label'] for r in regions}
        available = {(e['dimension'], e['tag']) for e in geometry.get('entities', [])}
        candidates = []
        for grp in geometry.get('groups', []):
            dim = grp.get('dimension')
            if dim in (1, 2):
                ents = [[dim, int(t)] for t in grp.get('entities', []) if (dim, int(t)) in available]
                if ents:
                    candidates.append((grp.get('name') or f"group_{grp['tag']}", ents, None))
        named_buckets = {}
        for ent in geometry.get('entities', []):
            dim = ent.get('dimension')
            cname = (ent.get('name') or '').strip()
            if dim in (1, 2) and cname:
                named_buckets.setdefault((dim, cname), []).append([dim, int(ent['tag'])])
        for (dim, cname), ents in sorted(named_buckets.items()):
            color_hint = next(
                (e.get('colour') for e in geometry['entities'] if [e['dimension'], e['tag']] in ents and e.get('colour')),
                None,
            )
            candidates.append((cname, ents, color_hint))
        added = 0
        for raw_label, ents, color_hint in candidates:
            base = raw_label.strip() or 'region'
            lbl = base
            suffix = 1
            while lbl in used_labels:
                lbl = f'{base}_{suffix}'
                suffix += 1
            if any(sorted(r['entities']) == sorted(ents) for r in regions):
                continue
            col = color_hint or AUTO_PALETTE[(len(regions)) % len(AUTO_PALETTE)]
            regions = put_region(regions, geometry, lbl, ents, col)
            used_labels.add(lbl)
            added += 1
        if not added:
            self.details.setText('No new physical groups or named CAD entities found to auto-create regions.')
            return
        self.window.boundary_regions = regions
        self.window.refresh_boundary_regions()
        self.refresh_list(regions[-1]['id'])
        self.load_region(self.regions.currentItem())
        self.details.setText(f'Auto-created {added} boundary region(s) from CAD groups/names.')

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
