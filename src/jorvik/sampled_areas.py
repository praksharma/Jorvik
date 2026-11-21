"""Sampled-region tree and independent point visibility controls."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem
from PySide6.QtGui import QColor, QIcon, QPixmap
from .sampling_methods import METHODS

REGION_COLOURS = ("#fff6dc", "#83e5ed", "#b5ef86", "#aabfff", "#ffffff")

ENTITY_NAMES = {1: 'Edge', 2: 'Face', 3: 'Volume'}


def area_details(area, status):
    entities = ', '.join(f"{ENTITY_NAMES[d]} {t}" for d, t in area.get('entities', []))
    if area['target'] == 'interior' and status != 'Generated':
        entities = 'Domain interior'
    count = (f"Points: {area['count']:,}" if status == 'Generated' else
             f"Requested points: {area.get('requested_count', area['count']):,}")
    details = [f'Entities: {entities}', count,
            f"Method: {METHODS[area['method']]}", f"Seed: {area['seed']}", f'Status: {status}']
    if status == 'Generated' and 'normal_status' in area:
        details.append('Normals: ' + area['normal_status'])
        if area.get('normal_validation'): details.append('Orientation: ' + area['normal_validation'])
        if area.get('normal_note'): details.append(area['normal_note'])
    if status == 'Generated' and area.get('domain_measure') is not None:
        details.append(f"Measure: {area['domain_measure']:.6g}")
    if status == 'Generated' and area.get('distance_status') == 'available':
        details.append('Boundary distance: available')
        dists = area.get('boundary_distances')
        if dists:
            details.append(f"d(x) range: [{min(dists):.4g}, {max(dists):.4g}]")
    if status == 'Generated' and area.get('adf_status') == 'available':
        regs = ', '.join(area.get('adf_region_labels', []))
        details.append(f"R-function ADF: available ({regs})")
        adfs = area.get('adf_distances')
        if adfs:
            details.append(f"ADF range: [{min(adfs):.4g}, {max(adfs):.4g}]")
    if status == 'Generated' and area.get('differential_geometry_status') == 'available':
        details.append('Darboux tangent frame: available')
        if area.get('curvature_status') == 'available' and area.get('mean_curvature'):
            h_vals = area['mean_curvature']
            details.append(f"Mean curvature H: [{min(h_vals):.4g}, {max(h_vals):.4g}]")
    if status == 'Generated' and area.get('spacetime_status') == 'available':
        tb = area.get('time_bounds', [0.0, 1.0])
        wins = area.get('causal_window_count', 1)
        details.append(f"Space-time: [{tb[0]:.4g}, {tb[1]:.4g}] ({wins} causal windows)")
    if status == 'Generated' and isinstance(area.get('pairing'), dict):
        p_info = area['pairing']
        details.append(f"Pairing: {p_info.get('pair_kind', 'paired')} ({p_info.get('partner_label', p_info.get('role', ''))})")
    return details


class SampledAreasTree(QTreeWidget):
    visibilityChanged = Signal()
    areaSelected = Signal(str)

    def __init__(self):
        super().__init__()
        self.setHeaderHidden(True)
        self.setAccessibleName('Sampled Areas')
        self.areas = {}
        self.items = {}
        self.itemChanged.connect(self.visibility_changed)
        self.currentItemChanged.connect(self.select_area)
        self.itemClicked.connect(self.select_area)
        self.clear_areas()

    def clear_areas(self):
        self.blockSignals(True)
        self.clear()
        self.areas.clear()
        self.items.clear()
        self.root = QTreeWidgetItem(self, ['Sampled Areas'])
        self.root.setFlags(self.root.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self.root.setExpanded(True)
        self.empty = QTreeWidgetItem(self.root, ['No sampled areas yet'])
        self.empty.setFlags(self.empty.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self.blockSignals(False)

    def set_area(self, area, status='Generated'):
        label = area['label']
        self.blockSignals(True)
        if self.empty is not None:
            self.root.removeChild(self.empty)
            self.empty = None
        item = self.items.get(label)
        if item is None:
            item = QTreeWidgetItem(self.root, [label])
            item.setData(0, Qt.ItemDataRole.UserRole, label)
            self.items[label] = item
            visible = True
        else:
            visible = item.checkState(0) == Qt.CheckState.Checked or self.areas[label][1] != 'Generated'
            item.takeChildren()
        if status == 'Generated':
            if 'colour' not in area:
                index = sum(s == 'Generated' for key, (_, s) in self.areas.items() if key != label)
                area['colour'] = REGION_COLOURS[index % len(REGION_COLOURS)]
            swatch = QPixmap(16, 16)
            swatch.fill(QColor(area['colour']))
            item.setIcon(0, QIcon(swatch))
        self.areas[label] = (area, status)
        flags = item.flags()
        if status == 'Generated':
            item.setFlags(flags | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked if visible else Qt.CheckState.Unchecked)
            item.setToolTip(0, 'Check to show these points; uncheck to hide them. Export includes hidden datasets.')
        else:
            item.setFlags(flags & ~Qt.ItemFlag.ItemIsUserCheckable)
            item.setData(0, Qt.ItemDataRole.CheckStateRole, None)
            item.setToolTip(0, status)
        for text in area_details(area, status):
            QTreeWidgetItem(item, [text])
        item.setExpanded(True)
        self.blockSignals(False)
        if self.currentItem() is item:
            self.areaSelected.emit(label)

    def rename_area(self, old, new):
        area, status = self.areas.pop(old)
        item = self.items.pop(old)
        area['label'] = new
        self.areas[new] = (area, status)
        self.items[new] = item
        self.blockSignals(True)
        item.setText(0, new)
        item.setData(0, Qt.ItemDataRole.UserRole, new)
        self.blockSignals(False)

    def colours(self):
        return {label: area['colour'] for label, (area, status) in self.areas.items()
                if status == 'Generated'}

    def hidden_labels(self):
        return [label for label, item in self.items.items()
                if self.areas[label][1] == 'Generated' and item.checkState(0) != Qt.CheckState.Checked]

    def visibility_changed(self, item, column):
        if item.data(0, Qt.ItemDataRole.UserRole) in self.areas:
            self.visibilityChanged.emit()

    def select_area(self, current, previous):
        while current is not None and current.parent() is not self.root:
            current = current.parent()
        if current is not None:
            label = current.data(0, Qt.ItemDataRole.UserRole)
            if label in self.areas:
                self.areaSelected.emit(label)
