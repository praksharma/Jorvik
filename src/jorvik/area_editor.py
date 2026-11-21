"""Edit a region's recipe explicitly; coordinates are replaced only after success."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QFormLayout,QLineEdit,QComboBox,
    QSpinBox,QListWidget,QListWidgetItem,QDialogButtonBox,QLabel,QMessageBox,QCheckBox)
from .sampling_methods import METHODS


class AreaEditor(QDialog):
    def __init__(self,window,label):
        super().__init__(window)
        self.window=window;self.original_label=label
        area,status=window.sampled_areas.areas[label]
        self._original_area = area
        self.setWindowTitle('Edit sampled area');self.resize(500,640)
        layout=QVBoxLayout(self);form=QFormLayout();layout.addLayout(form)
        self.label=QLineEdit(label);form.addRow('Label',self.label)
        self.target=QComboBox();self.target.addItem('Faces','face');self.target.addItem('Edges','edge');self.target.addItem('Domain interior','interior')
        self.target.setCurrentIndex(2 if area['target']=='interior' else (0 if area['entities'][0][0]==2 else 1));form.addRow('Target',self.target)
        self.method=QComboBox()
        for key,title in METHODS.items():self.method.addItem(title,key)
        self.method.setCurrentIndex(self.method.findData(area['method']));form.addRow('Method',self.method)
        self.count=QSpinBox();self.count.setRange(1,50000);self.count.setValue(area.get('requested_count',area['count']));form.addRow('Points / grid budget',self.count)
        self.seed=QSpinBox();self.seed.setRange(0,2147483647);self.seed.setValue(area['seed']);form.addRow('Seed',self.seed)
        self.resolution=QComboBox()
        resolutions=[('Standard',32),('Fine',64)]
        if area.get('resolution')==16:resolutions.insert(0,('Coarse',16))
        for title,value in resolutions:self.resolution.addItem(title,value)
        self.resolution.setCurrentIndex(max(0,self.resolution.findData(area.get('resolution',32))));form.addRow('Accuracy',self.resolution)
        self.compute=QComboBox();self.compute.addItem('Multicore','multicore');self.compute.addItem('Reproducible','reproducible')
        self.compute.setCurrentIndex(self.compute.findData(area.get('compute_mode','reproducible')));form.addRow('Compute mode',self.compute)
        self.cores=QSpinBox();self.cores.setRange(1,64);self.cores.setValue(area.get('cpu_threads',1));form.addRow('CPU cores',self.cores)
        self.include_differential_geometry = QCheckBox('Darboux tangents & curvatures (H, K)')
        self.include_differential_geometry.setChecked(area.get('differential_geometry_status') == 'available')
        form.addRow('PINN tensors', self.include_differential_geometry)
        self.include_adf = QCheckBox('Smooth R-function ADF & transfinite weights')
        self.include_adf.setChecked(area.get('adf_status') == 'available')
        form.addRow('', self.include_adf)
        self.include_spacetime = QCheckBox('Space-time QMC [t₀, T] & causal windows')
        self.include_spacetime.setChecked(area.get('spacetime_status') == 'available')
        form.addRow('', self.include_spacetime)
        self.area_refinement = area.get('refinement')
        self.entity_filter = QLineEdit()
        self.entity_filter.setPlaceholderText('Filter entities by tag, type or CAD name…')
        self.entity_filter.setClearButtonEnabled(True)
        self.entity_filter.textChanged.connect(self.filter_entities)
        layout.addWidget(self.entity_filter)
        self.entities=QListWidget();layout.addWidget(self.entities)
        self.selected_keys={tuple(e) for e in area['entities']}
        self.target.currentIndexChanged.connect(self.populate_entities);self.populate_entities()
        note=QLabel('Regenerate replaces this area only after successful generation. Cancel or failure keeps its existing points, colour and visibility.');note.setWordWrap(True);layout.addWidget(note)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Regenerate area')
        buttons.accepted.connect(self.apply);buttons.rejected.connect(self.reject);layout.addWidget(buttons)

    def populate_entities(self):
        for i in range(self.entities.count()):
            item=self.entities.item(i);key=tuple(item.data(Qt.ItemDataRole.UserRole))
            if item.checkState()==Qt.CheckState.Checked:self.selected_keys.add(key)
            else:self.selected_keys.discard(key)
        self.entities.clear();kind=self.target.currentData();self.entities.setEnabled(kind!='interior')
        self.entity_filter.setEnabled(kind!='interior')
        dimension=2 if kind=='face' else 1
        if kind!='interior':
            for entity in self.window.geometry['entities']:
                if entity['dimension']!=dimension:continue
                key=(dimension,entity['tag'])
                title=f"{kind.title()} {key[1]} · {entity['type']}"
                if entity.get('name'):
                    title += f" ({entity['name']})"
                item=QListWidgetItem(title,self.entities)
                item.setData(Qt.ItemDataRole.UserRole,key);item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if key in self.selected_keys else Qt.CheckState.Unchecked)
        self.filter_entities(self.entity_filter.text())

    def filter_entities(self, query=''):
        needle = str(query).strip().lower()
        for i in range(self.entities.count()):
            item = self.entities.item(i)
            item.setHidden(bool(needle and needle not in item.text().lower()))

    def apply(self):
        label=self.label.text().strip()
        if not label or (label!=self.original_label and label in self.window.sampled_areas.areas):
            QMessageBox.warning(self,'Dataset label','Enter a non-empty, unique label.');return
        entities=[list(self.entities.item(i).data(Qt.ItemDataRole.UserRole)) for i in range(self.entities.count()) if self.entities.item(i).checkState()==Qt.CheckState.Checked]
        interior=self.target.currentData()=='interior'
        if not interior and not entities:
            QMessageBox.warning(self,'Sampling target','Choose at least one face or edge.');return
        request={k:self.window.geometry[k] for k in ('document','source','source_hash')}
        for key in ('source_format', 'import_options', 'coordinate_units', 'source_dependencies'):
            if key in self.window.geometry:
                request[key] = self.window.geometry[key]
        request.update(label=label,target='interior' if interior else 'selection',entities=[] if interior else entities,
                       method=self.method.currentData(),count=self.count.value(),seed=self.seed.value(),resolution=self.resolution.currentData(),
                       compute_mode=self.compute.currentData(),cpu_threads=self.cores.value() if self.compute.currentData()=='multicore' else 1)
        if self.area_refinement is not None:
            request['refinement'] = self.area_refinement
        if self.include_differential_geometry.isChecked() and not interior:
            request['include_differential_geometry'] = True
        if self.include_adf.isChecked():
            request['include_adf'] = True
            request['all_entities'] = [
                {'dimension': e['dimension'], 'tag': e['tag']}
                for e in self.window.geometry.get('entities', [])
            ]
            if self.window.boundary_regions:
                request['adf_regions'] = [
                    {'label': r['label'], 'entities': r['entities']}
                    for r in self.window.boundary_regions
                ]
        if self.include_spacetime.isChecked():
            request['spacetime'] = {
                'enabled': True,
                'time_bounds': self._original_area.get('time_bounds', [0.0, 1.0]),
                'causal_windows': int(self._original_area.get('causal_window_count', 8)),
            }
        self.window.sampling.generate_request(request,replace_label=self.original_label)
        self.accept()
