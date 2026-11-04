"""Jorvik desktop shell, adapted from PyniteGUI's Qt layout."""
import argparse
import sys
from pathlib import Path
import json
from PySide6.QtCore import QProcess, QSettings, Qt, QSize, QSignalBlocker, QTimer
from PySide6.QtGui import QActionGroup, QColor
from PySide6.QtWidgets import (QApplication, QComboBox, QDockWidget, QLabel,
    QMainWindow, QMessageBox, QToolBar, QTreeWidget, QTreeWidgetItem, QFileDialog, QAbstractItemView, QScrollArea, QSizePolicy, QSplitter, QMenu, QInputDialog, QColorDialog, QPushButton, QWidget, QVBoxLayout)
from . import __version__
from .theme import configure_theme
from .viewport import Viewport
from .graphics import configure_graphics
from .window_controls import install_window_controls
from .sampling_panel import SamplingPanel
from .sampled_areas import SampledAreasTree, area_details
from .icons import tool_icon
from .cad_import import FILE_FILTER
from .project_ui import ProjectActions
from .history import HistoryActions, undoable


class MainWindow(HistoryActions, ProjectActions, QMainWindow):
    def __init__(self, settings=None):
        super().__init__()
        self.settings = settings if settings is not None else QSettings("Jorvik", "Jorvik")
        self._close_requested = False
        self._handling_close = False
        self.geometry = None
        self.project_path = None
        self.saved_digest = None
        self.saving_project = False
        self.project_io = None
        self.project_cache = None
        self.parametrisation_setup = None
        self.boundary_regions = []
        self.boundary_dialog = None
        self.import_process = None
        self.selection = set()
        self.selected_area = None
        self.icon_actions = []
        self.items = {}
        self.setWindowTitle("Jorvik — Untitled")
        self.resize(1280, 820)
        self.setMinimumSize(820, 560)
        self.view = Viewport(self)
        self.setCentralWidget(self.view)
        self.statusBar().showMessage("Starting 3D viewport…")
        self.view.status.connect(self.statusBar().showMessage)
        file_menu = self.menuBar().addMenu("&File")
        self.open_action = file_menu.addAction("&Open Geometry…", self.open_geometry, "Ctrl+O")
        self.open_project_action = file_menu.addAction('Open project…', self.open_project, 'Ctrl+Shift+O')
        self.save_project_action = file_menu.addAction('Save project', self.save_project, 'Ctrl+S')
        self.save_project_as_action = file_menu.addAction('Save project as…', lambda: self.save_project(True), 'Ctrl+Shift+S')
        file_menu.addSeparator()
        self.export_action = file_menu.addAction("&Export samples…", self.export_samples, "Ctrl+Alt+E")
        self.export_action.setEnabled(False)
        file_menu.addAction("&Quit", self.close, "Ctrl+Q")
        self.view_menu = self.menuBar().addMenu("&View")
        self.toolbar = QToolBar("Viewport", self)
        self.toolbar.setObjectName("viewport_toolbar")
        self.addToolBar(self.toolbar)
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.toolbar.setIconSize(QSize(24,24))
        for action, icon in ((self.open_action,'open'),(self.export_action,'export')):
            action.setToolTip(action.text().replace('&',''))
            self.icon_actions.append((action,icon))
            self.toolbar.addAction(action)
        self.toolbar.addSeparator()
        self.orientation = QComboBox()
        self.orientation.addItems(["Isometric", "Front XY", "Top XZ", "Right YZ", "Back XY", "Bottom XZ", "Left YZ", "Orbit"])
        self.orientation.model().item(7).setEnabled(False)
        self.orientation.setToolTip("Camera orientation")
        self.orientation.activated.connect(lambda index: self.view.call("orient", index))
        self.view.oriented.connect(self.orientation.setCurrentIndex)
        self.toolbar.addWidget(self.orientation)
        self.tool_action("Reset view", "reset", "Fit the geometry in the isometric view", lambda: self.view.call("reset"))
        self.toolbar.addSeparator()
        for label, attribute in (("Grid", "show_grid"), ("Axes", "show_axes")):
            action = self.tool_action(label, label.lower(), f"Show/hide {label.lower()}")
            action.setCheckable(True)
            action.setChecked(getattr(self.view, attribute))
            action.toggled.connect(lambda checked, attr=attribute: self.set_visibility(attr, checked))
        self.toolbar.addSeparator()
        self.selection_tools = QActionGroup(self)
        self.selection_actions = {}
        for mode, label, shortcut in (("face", "Select faces", "F"), ("edge", "Select edges", "E")):
            action = self.tool_action(label, mode, label)
            action.setCheckable(True)
            action.setChecked(mode == "face")
            action.setShortcut(shortcut)
            action.setToolTip(label + " — click to select; Ctrl/Shift-click to add; drag to orbit")
            action.triggered.connect(lambda checked=False, mode=mode: self.set_selection_mode(mode))
            self.selection_tools.addAction(action)
            self.selection_actions[mode] = action
        self.tool_action("Clear selection", "clear", "Clear CAD entity selection (Esc)", self.clear_selection).setShortcut("Esc")
        self.toolbar.addSeparator()
        visibility_menu = self.view_menu.addMenu('Visibility')
        self.visibility_actions = {}
        for key, label, icon, hint in (
            ('geometry','Geometry','geometry','Show/hide all geometry without changing selections'),
            ('faces','Faces','faces','Show/hide shaded faces; keep enabled edges'),
            ('edges','Edges','edges','Show/hide edges and their highlights')):
            action = self.tool_action(label,icon,hint)
            action.setCheckable(True);action.setChecked(True)
            action.toggled.connect(lambda checked, key=key: self.view.set_geometry_visibility({key:checked}))
            self.visibility_actions[key] = action
            visibility_menu.addAction(action)
        self.samples_action = self.tool_action('All samples','samples','Show/hide all generated sample points')
        self.samples_action.setCheckable(True);self.samples_action.setChecked(True)
        visibility_menu.addAction(self.samples_action)
        self.region_samples_action = self.tool_action('Selected region samples','region','Show/hide only the selected Sampled Areas region')
        self.region_samples_action.setCheckable(True);self.region_samples_action.setEnabled(False)
        self.region_samples_action.toggled.connect(self.set_region_samples_visible)
        visibility_menu.addAction(self.region_samples_action)
        self.toolbar.addSeparator();visibility_menu.addSeparator()
        self.isolate_action = self.tool_action('Isolate region','isolate','Isolate the selected Sampled Areas label; toggle off to restore visibility')
        self.isolate_action.setCheckable(True);self.isolate_action.setEnabled(False)
        self.isolate_action.toggled.connect(self.update_isolation)
        self.isolate_geometry_action = self.tool_action('Geometry in isolation','context','Include only the isolated region’s geometry; otherwise show its points alone')
        self.isolate_geometry_action.setCheckable(True)
        self.isolate_geometry_action.setEnabled(False)
        self.isolate_geometry_action.toggled.connect(self.update_isolation)
        visibility_menu.addAction(self.isolate_action);visibility_menu.addAction(self.isolate_geometry_action)
        self.view.picked.connect(self.pick_entity)
        tree = QTreeWidget()
        self.tree = tree
        tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        tree.itemSelectionChanged.connect(self.tree_selection_changed)
        tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tree.customContextMenuRequested.connect(self.geometry_context_menu)
        tree.setHeaderHidden(True)
        tree.addTopLevelItems([QTreeWidgetItem(["Geometry"]), QTreeWidgetItem(["Regions"])])
        self.sampled_areas = SampledAreasTree()
        self.sampled_areas.areaSelected.connect(self.show_sampled_area)
        self.sampled_areas.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sampled_areas.customContextMenuRequested.connect(self.area_context_menu)
        self.left_splitter = QSplitter(Qt.Orientation.Vertical)
        self.left_splitter.addWidget(tree)
        self.left_splitter.addWidget(self.sampled_areas)
        self.left_splitter.setChildrenCollapsible(False)
        self.left_splitter.setSizes([350, 350])
        sidebar = QWidget()
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.addWidget(self.left_splitter)
        self.boundary_button = QPushButton('Boundary regions…')
        self.boundary_button.clicked.connect(self.open_boundary_regions)
        sidebar_layout.addWidget(self.boundary_button)
        self.parametrisation_button = QPushButton('Parametrisation…')
        self.parametrisation_button.setToolTip('Configure, preview and export translation, rotation and scaling of sampled datasets')
        self.parametrisation_button.clicked.connect(self.open_parametrisation)
        sidebar_layout.addWidget(self.parametrisation_button)
        self.parametrisation_dialog = None
        self.geometry_dock = self.dock("Geometry and Sampled Areas", "geometry", sidebar, Qt.DockWidgetArea.LeftDockWidgetArea)
        properties = QLabel("No geometry selected.")
        self.properties = properties
        properties.setWordWrap(True)
        properties.setTextFormat(Qt.TextFormat.PlainText)
        properties.setMargin(16)
        properties.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.properties_dock = self.dock("Properties", "properties", self.scrollable_text(properties), Qt.DockWidgetArea.RightDockWidgetArea)
        self.sampling = SamplingPanel(self)
        self.samples_action.toggled.connect(self.sampling.show_points.setChecked)
        self.sampling.show_points.toggled.connect(self.samples_action.setChecked)
        self.sampled_areas.visibilityChanged.connect(self.sampling.refresh_style)
        self.sampled_areas.visibilityChanged.connect(self.sync_region_visibility)
        self.sampling_dock = self.dock("Sampling", "sampling", self.sampling, Qt.DockWidgetArea.RightDockWidgetArea)
        self.tabifyDockWidget(self.properties_dock, self.sampling_dock)
        self.sampling_dock.raise_()
        self.view_menu.addAction(self.toolbar.toggleViewAction())
        self.view_menu.addSeparator()
        self.view_menu.addAction("Reset layout", self.reset_layout)
        appearance = self.view_menu.addMenu("Appearance")
        self.theme_group = QActionGroup(self)
        saved_theme = self.settings.value("theme", "light")
        for name in ("light", "dark"):
            action = appearance.addAction(name.title())
            action.setCheckable(True)
            action.setChecked(name == saved_theme)
            action.triggered.connect(lambda checked=False, name=name: self.set_theme(name))
            self.theme_group.addAction(action)
        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction("Graphics information", self.graphics_information)
        help_menu.addAction("About Jorvik", self.about)
        self.resizeDocks([self.geometry_dock, self.properties_dock], [240, 260], Qt.Orientation.Horizontal)
        if self.settings.contains("geometry"):
            self.restoreGeometry(self.settings.value("geometry"))
        if self.settings.contains("layout"):
            self.restoreState(self.settings.value("layout"))
        self.refresh_icons()
        self.refresh_project_actions()
        self.initialize_history()
        self.initialize_recovery()

    def open_boundary_regions(self):
        if self.project_busy() or not self.geometry:
            return
        from .regions_panel import BoundaryRegionsDialog
        self.boundary_dialog = BoundaryRegionsDialog(self)
        self.boundary_dialog.finished.connect(self.finish_boundary_regions)
        self.open_action.setEnabled(False)
        self.refresh_project_actions()
        self.boundary_dialog.open()

    def finish_boundary_regions(self):
        dialog, self.boundary_dialog = self.boundary_dialog, None
        self.open_action.setEnabled(True)
        self.refresh_project_actions()
        if dialog is not None: dialog.deleteLater()

    def refresh_boundary_regions(self):
        self.view.set_boundary_regions(self.boundary_regions)
        self.sampling.refresh_targets()
        if self.geometry: self.update_selection()

    def open_parametrisation(self):
        if self.sampling.process or self.import_process or self.saving_project or self.project_io is not None:
            QMessageBox.information(self, 'Parametrisation', 'Wait for current processing to finish.')
            return
        if not self.sampling.batches:
            QMessageBox.information(self, 'Parametrisation', 'Generate boundary or interior samples first.')
            return
        if self.parametrisation_dialog is not None:
            self.parametrisation_dialog.show()
            self.parametrisation_dialog.raise_()
            self.parametrisation_dialog.activateWindow()
            return
        from .parametrisation_panel import ParametrisationDialog
        self.parametrisation_dialog = ParametrisationDialog(self)
        # Keep the viewport interactive while protecting the preview's originals.
        self.sampling.setEnabled(False)
        self.tree.setEnabled(False)
        self.sampled_areas.setEnabled(False)
        self.open_action.setEnabled(False)
        self.export_action.setEnabled(False)
        self.parametrisation_dialog.finished.connect(self.finish_parametrisation)
        self.parametrisation_dialog.show()
        self.refresh_project_actions()

    @undoable('change transform setup')
    def finish_parametrisation(self):
        dialog = self.parametrisation_dialog
        self.parametrisation_dialog = None
        if dialog is not None:
            try:
                from .parametrisation import validate_setup
                candidate = dialog.setup()
                validate_setup(candidate, self.sampling.batches)
                self.parametrisation_setup = candidate
            except (ValueError, TypeError, OverflowError) as error:
                self.statusBar().showMessage('Setup not saved; previous valid setup retained: ' + str(error))
        self.sampling.setEnabled(True)
        self.tree.setEnabled(True)
        self.sampled_areas.setEnabled(True)
        self.open_action.setEnabled(True)
        self.export_action.setEnabled(bool(self.sampling.batches))
        self.refresh_project_actions()
        if dialog is not None:
            dialog.deleteLater()

    def tool_action(self, title, icon, tooltip, callback=None):
        action = self.toolbar.addAction(title)
        action.setToolTip(tooltip)
        action.setStatusTip(tooltip)
        if callback:
            action.triggered.connect(callback)
        self.icon_actions.append((action,icon))
        return action

    def refresh_icons(self):
        colour = self.palette().windowText().color().name()
        for action, name in self.icon_actions:
            action.setIcon(tool_icon(name,colour))

    def sync_region_visibility(self):
        item = self.sampled_areas.items.get(self.selected_area)
        generated = bool(item and self.sampled_areas.areas[self.selected_area][1]=='Generated')
        self.region_samples_action.setEnabled(generated)
        with QSignalBlocker(self.region_samples_action):
            self.region_samples_action.setChecked(generated and item.checkState(0)==Qt.CheckState.Checked)

    def set_region_samples_visible(self, visible):
        if self.selected_area in self.sampled_areas.items:
            self.sampled_areas.items[self.selected_area].setCheckState(0,Qt.CheckState.Checked if visible else Qt.CheckState.Unchecked)

    def isolated_label(self):
        return self.selected_area if self.isolate_action.isChecked() else None

    def clear_isolation(self):
        self.selected_area = None
        self.isolate_action.setChecked(False)
        self.isolate_action.setEnabled(False)
        self.region_samples_action.setEnabled(False)
        with QSignalBlocker(self.region_samples_action):
            self.region_samples_action.setChecked(False)
        self.view.set_geometry_visibility({'isolatedEntities':None})

    def update_isolation(self):
        self.isolate_geometry_action.setEnabled(self.isolate_action.isChecked())
        entities = None
        label = self.isolated_label()
        if label:
            area, status = self.sampled_areas.areas[label]
            entities = []
            if self.isolate_geometry_action.isChecked():
                entities = [list(key) for key in area['entities']]
                if area['entity_dimension'] == 3:
                    # Interior generation currently targets all domain volumes.
                    entities = [[e['dimension'],e['tag']] for e in self.geometry['entities']]
                else:
                    keys = {tuple(key) for key in entities}
                    entities += [boundary for e in self.geometry['entities']
                                 if (e['dimension'],e['tag']) in keys for boundary in e['boundary'] if boundary[0]==1]
        self.view.set_geometry_visibility({'isolatedEntities':entities})
        if hasattr(self,'sampling'):
            self.sampling.refresh_style()

    @staticmethod
    def scrollable_text(label):
        label.setAlignment(Qt.AlignmentFlag.AlignTop)
        label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(label)
        return scroll

    def dock(self, title, name, widget, area):
        dock = QDockWidget(title, self)
        dock.setObjectName(name)
        dock.setWidget(widget)
        self.addDockWidget(area, dock)
        self.view_menu.addAction(dock.toggleViewAction())
        return dock

    def geometry_context_menu(self, position):
        item = self.tree.itemAt(position)
        key = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if key and key[0] == 2 and not item.isSelected():
            self.tree.clearSelection()
            self.tree.setCurrentItem(item)
        menu = self.face_menu()
        menu.exec(self.tree.viewport().mapToGlobal(position))
        menu.deleteLater()

    def face_menu(self):
        menu = QMenu(self.tree)
        faces = sorted(tag for dimension, tag in self.selection if dimension == 2)
        hide = menu.addAction('Hide selected faces', lambda: self.hide_faces(faces))
        isolate = menu.addAction('Isolate selected faces', lambda: self.isolate_faces(faces))
        hide.setEnabled(bool(faces)); isolate.setEnabled(bool(faces))
        menu.addSeparator()
        menu.addAction('Show all faces', self.show_all_faces).setEnabled(self.geometry is not None)
        return menu

    def hide_faces(self, tags):
        hidden = set(self.view.geometry_visibility['hiddenFaces']) | set(tags)
        self.view.set_geometry_visibility({'hiddenFaces': sorted(hidden)})

    def isolate_faces(self, tags):
        self.isolate_action.setChecked(False)
        for key in ('geometry', 'faces'):
            self.visibility_actions[key].setChecked(True)
        hidden = set(self.view.geometry_visibility['hiddenFaces']) - set(tags)
        self.view.set_geometry_visibility({'hiddenFaces': sorted(hidden), 'isolatedFaces': list(tags)})

    def show_all_faces(self):
        self.isolate_action.setChecked(False)
        for key in ('geometry', 'faces'):
            self.visibility_actions[key].setChecked(True)
        self.view.set_geometry_visibility({'hiddenFaces': [], 'isolatedFaces': None})

    def area_context_menu(self, position):
        item = self.sampled_areas.itemAt(position)
        while item is not None and item.parent() is not self.sampled_areas.root:
            item = item.parent()
        if item is None:
            return
        label = item.data(0, Qt.ItemDataRole.UserRole)
        self.sampled_areas.setCurrentItem(item)
        menu = self.area_menu(label)
        menu.exec(self.sampled_areas.viewport().mapToGlobal(position))
        menu.deleteLater()

    def area_menu(self, label):
        menu = QMenu(self.sampled_areas)
        area, status = self.sampled_areas.areas[label]
        menu.addAction('Rename…', lambda: self.rename_area(label)).setEnabled(not self.sampling.process)
        menu.addAction('Change colour…', lambda: self.change_area_colour(label)).setEnabled(status == 'Generated')
        menu.addAction('Edit…', lambda: self.edit_area(label)).setEnabled(not self.sampling.process and not self.parametrisation_dialog)
        menu.addAction('Duplicate dataset', lambda: self.duplicate_area(label)).setEnabled(status == 'Generated' and not self.project_busy())
        menu.addAction('Delete dataset', lambda: self.delete_area(label)).setEnabled(not self.project_busy())
        menu.addSeparator()
        visible = menu.addAction('Show points')
        visible.setCheckable(True)
        visible.setChecked(self.sampled_areas.items[label].checkState(0) == Qt.CheckState.Checked)
        visible.setEnabled(status == 'Generated')
        visible.toggled.connect(lambda checked: self.sampled_areas.items[label].setCheckState(
            0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked))
        isolate = menu.addAction('Isolate region', lambda: self.isolate_area(label))
        isolate.setEnabled(status == 'Generated')
        menu.addAction('Show all regions', self.show_all_regions)
        return menu

    def isolate_area(self, label):
        self.show_sampled_area(label)
        self.isolate_action.setChecked(True)

    def show_all_regions(self):
        self.isolate_action.setChecked(False)
        self.samples_action.setChecked(True)
        for label, item in self.sampled_areas.items.items():
            if self.sampled_areas.areas[label][1] == 'Generated':
                item.setCheckState(0, Qt.CheckState.Checked)

    def edit_area(self, label):
        if self.project_busy():
            return
        from .area_editor import AreaEditor
        dialog = AreaEditor(self,label)
        dialog.exec()
        dialog.deleteLater()

    @undoable('rename dataset')
    def rename_area(self, label):
        if self.project_busy():
            return
        name, accepted = QInputDialog.getText(self, 'Rename sampled area', 'Dataset label:', text=label)
        if not accepted:
            return
        name = name.strip()
        if not name or (name != label and name in self.sampled_areas.areas):
            QMessageBox.warning(self, 'Dataset label', 'Enter a non-empty, unique label.')
            return
        if name == label:
            return
        self.sampled_areas.rename_area(label, name)
        self.remap_parameter_label(label, name)
        if self.selected_area == label:
            self.selected_area = name
            self.show_sampled_area(name)
        self.sampling.label.setText(self.sampling.next_available_label(self.sampling.label.text()))
        self.sampling.refresh_preview()

    @undoable('change dataset colour')
    def change_area_colour(self, label):
        if self.project_busy(): return
        area, status = self.sampled_areas.areas[label]
        if status != 'Generated':
            return
        colour = QColorDialog.getColor(QColor(area['colour']), self, 'Sampled area colour')
        if colour.isValid():
            area['colour'] = colour.name()
            self.sampled_areas.set_area(area, status)
            self.sampling.refresh_style()

    def show_sampled_area(self, label):
        area, status = self.sampled_areas.areas[label]
        self.selected_area = label
        self.sync_region_visibility()
        if status != 'Generated':
            self.isolate_action.setChecked(False)
        self.isolate_action.setEnabled(status == 'Generated')
        if self.isolate_action.isChecked():
            self.update_isolation()
        text = label + '\n\n' + '\n'.join(area_details(area, status))
        if area.get('notes'):
            text += '\n\n' + '\n'.join(area['notes'])
        self.properties.setText(text)
        self.properties_dock.raise_()

    def export_samples(self):
        if not self.sampling.batches or self.sampling.process:
            return
        directory = self.settings.value('export/directory', str(Path.home()))
        stem = Path(self.geometry['source']).stem if self.geometry else 'geometry'
        filename, selected_filter = QFileDialog.getSaveFileName(
            self, 'Export samples', str(Path(directory) / f'{stem}_samples'),
            'NetCDF (*.nc);;NumPy archive (*.npz)')
        if not filename:
            return
        path = Path(filename)
        if not path.suffix:
            path = path.with_suffix('.npz' if 'npz' in selected_filter else '.nc')
        try:
            # Keep scientific data dependencies out of application startup.
            from .export import export_samples
            export_samples(self.sampling.batches, path)
        except (OSError, ValueError, TypeError, KeyError, ImportError) as error:
            QMessageBox.warning(self, 'Export failed', str(error))
            return
        self.settings.setValue('export/directory', str(path.parent))
        self.statusBar().showMessage(f'Exported {len(self.sampling.batches)} datasets to {path}')

    def set_visibility(self, attribute, checked):
        setattr(self.view, attribute, checked)
        self.view.apply_theme()

    def reset_layout(self):
        for dock, area in ((self.geometry_dock, Qt.DockWidgetArea.LeftDockWidgetArea),
                           (self.properties_dock, Qt.DockWidgetArea.RightDockWidgetArea)):
            dock.setFloating(False)
            self.addDockWidget(area, dock)
            dock.show()
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.toolbar)
        self.toolbar.show()
        self.sampling_dock.setFloating(False)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.sampling_dock)
        self.tabifyDockWidget(self.properties_dock, self.sampling_dock)
        self.sampling_dock.show()
        self.sampling_dock.raise_()
        self.resizeDocks([self.geometry_dock, self.properties_dock], [240, 260], Qt.Orientation.Horizontal)

    def set_theme(self, name):
        configure_theme(QApplication.instance(), name)
        self.settings.setValue("theme", name)
        self.refresh_icons()

    def apply_theme(self):
        self.view.apply_theme()

    def graphics_information(self):
        if not self.view.is_ready:
            QMessageBox.information(self, "Graphics information", "The 3D renderer is not ready.")
            return
        def received(value):
            try:
                info = json.loads(value)
                text = f"Renderer: {info['renderer']}\n\nVendor: {info['vendor']}\n\n{info['version']}"
                if info['lost']:
                    text += "\n\nGraphics context lost."
            except (TypeError, ValueError, KeyError):
                text = "Renderer information is unavailable."
            QMessageBox.information(self, "Graphics information", text)
        self.view.web.page().runJavaScript("JSON.stringify(window.geoViewer.diagnostics())", received)

    def about(self):
        QMessageBox.about(self, "About Jorvik", f"Jorvik {__version__}\nGeometry-aware sampling for PINNs.\n\nGmsh geometry import with face and edge selection. Seven sampling methods with boundary/interior previews.\nAdapted from PyniteGUI; licensed under AGPL-3.0-only.\nThree.js is distributed under its MIT licence.")

    def open_geometry(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open geometry", "", FILE_FILTER)
        if path:
            self.load_geometry(path)

    def load_geometry(self, path, *, on_error=None):
        if self.import_process is not None or self.sampling.process is not None or self.saving_project or self.project_io is not None:
            return
        process = QProcess(self)
        self.import_process = process
        self.refresh_project_actions()
        self.open_action.setEnabled(False)
        self.sampling.generate.setEnabled(False)
        self.statusBar().showMessage("Opening " + Path(path).name + "…")
        process.finished.connect(lambda code, status: self.import_finished(process, code, on_error))
        process.errorOccurred.connect(lambda error: self.import_failed_start(process, on_error) if error == QProcess.ProcessError.FailedToStart else None)
        process.start(sys.executable, ["-m", "jorvik.geometry", str(path)])

    def import_failed_start(self, process, on_error=None):
        if self.import_process is process:
            self.import_finished(process, -1, on_error)

    def import_finished(self, process, code, on_error=None):
        if self.import_process is not process:
            return
        self.import_process = None
        self.refresh_project_actions()
        self.open_action.setEnabled(True)
        self.sampling.generate.setEnabled(self.geometry is not None)
        try:
            if code:
                raise ValueError(bytes(process.readAllStandardError()).decode(errors="replace")[-2000:] or "Geometry loader could not start.")
            geometry = json.loads(bytes(process.readAllStandardOutput()))
            if not self.confirm_unsaved_changes():
                self.statusBar().showMessage('Opening cancelled — current workspace retained')
                return
            empty_project_path = self.project_path if self.geometry is None else None
            self.show_geometry(geometry)
            if empty_project_path is not None:
                self.project_path = empty_project_path
                self.saved_digest = None  # Importing is an unsaved edit of this document.
                self.setWindowTitle('Jorvik — ' + self.project_path.name)
        except (ValueError, KeyError) as error:
            self.statusBar().showMessage("Import failed — previous geometry retained")
            if on_error is not None:
                on_error(str(error))
            else:
                QMessageBox.warning(self, "Could not open geometry", str(error))
        finally:
            process.deleteLater()

    def show_geometry(self, geometry):
        self._history_suspended = True
        self.clear_recovery()
        self.geometry = geometry
        self.boundary_regions = []
        self.view.set_boundary_regions([])
        self.refresh_project_actions()
        self.project_path = None
        self.parametrisation_setup = None
        self.view.translation_preview = None
        self.view.camera_state = None
        self.selection.clear()
        self.items.clear()
        self.tree.blockSignals(True)
        self.tree.clear()
        if geometry is None:
            self.tree.blockSignals(False)
            self.view.set_geometry_visibility({'hiddenFaces': [], 'isolatedFaces': None, 'isolatedEntities': None})
            self.view.set_geometry(None)
            self.sampling.clear_batches()
            self.sampling.refresh_targets()
            self.sampling.generate.setEnabled(False)
            self.sampling.summary.setText('Open geometry to begin.')
            self.setWindowTitle('Jorvik — Untitled')
            self.reset_history()
            self.mark_project_clean()
            return
        parent = QTreeWidgetItem(self.tree, [geometry["name"]])
        for dimension, label in ((2, "Faces"), (1, "Edges")):
            group = QTreeWidgetItem(parent, [label])
            for entity in geometry["entities"]:
                if entity["dimension"] != dimension:
                    continue
                key = (dimension, entity["tag"])
                item = QTreeWidgetItem(group, [f"{label[:-1]} {key[1]} · {entity['type']}"])
                item.setData(0, Qt.ItemDataRole.UserRole, key)
                hints = []
                if entity.get("name"):
                    hints.append("CAD name: " + entity["name"])
                if entity.get("degenerate"):
                    hints.append("Zero-length CAD edge: retained topology; cannot be sampled.")
                if hints:
                    item.setToolTip(0, "\n".join(hints))
                self.items[key] = item
        regions = QTreeWidgetItem(self.tree, ["Imported groups"])
        for group in geometry["groups"]:
            if group["dimension"] in (1, 2):
                label = group["name"] or f"Group {group['tag']}"
                item = QTreeWidgetItem(regions, [f"{label} · {'edges' if group['dimension'] == 1 else 'faces'}"])
                item.setToolTip(0, "Entities: " + ", ".join(map(str, group["entities"])))
        parent.setExpanded(True)
        self.tree.blockSignals(False)
        self.view.set_geometry_visibility({'hiddenFaces': [], 'isolatedFaces': None})
        self.view.set_geometry(geometry)
        self.sampling.geometry_loaded()
        self.setWindowTitle("Jorvik — " + geometry["name"])
        self.update_selection()
        faces = sum(e["dimension"] == 2 for e in geometry["entities"])
        edges = sum(e["dimension"] == 1 for e in geometry["entities"])
        self.statusBar().showMessage(f"{geometry['name']} | {faces} faces · {edges} edges | {self.view.mode.title()} selection")
        self.reset_history()
        self.mark_project_clean()

    def set_selection_mode(self, mode):
        self.view.set_mode(mode)
        self.selection_actions[mode].setChecked(True)
        self.clear_selection()
        self.statusBar().showMessage(f"Select {mode}s | Click to select · Ctrl/Shift-click to add · Drag to orbit")

    def pick_entity(self, dimension, tag, extend):
        key = (dimension, tag)
        if key not in self.items:
            if not extend:
                self.clear_selection()
            return
        if dimension != (2 if self.view.mode == "face" else 1):
            return
        if extend:
            self.selection.symmetric_difference_update({key})
        else:
            self.selection = {key}
        self.update_selection()

    def clear_selection(self):
        self.selection.clear()
        self.update_selection()

    def tree_selection_changed(self):
        keys = {tuple(item.data(0, Qt.ItemDataRole.UserRole)) for item in self.tree.selectedItems()
                if item.data(0, Qt.ItemDataRole.UserRole)}
        current = self.tree.currentItem()
        key = current.data(0, Qt.ItemDataRole.UserRole) if current else None
        if key:
            mode = "face" if key[0] == 2 else "edge"
            self.view.set_mode(mode)
            self.selection_actions[mode].setChecked(True)
        dimension = 2 if self.view.mode == "face" else 1
        self.selection = {key for key in keys if key[0] == dimension}
        self.update_selection()

    def update_selection(self):
        self.tree.blockSignals(True)
        for key, item in self.items.items():
            item.setSelected(key in self.selection)
            if key in self.selection:
                item.parent().setExpanded(True)
        self.tree.blockSignals(False)
        self.view.set_selection([list(key) for key in sorted(self.selection)])
        if not self.selection:
            self.properties.setText("No entity selected.\n\nChoose Select faces (F) or Select edges (E), then click the geometry.")
            return
        lines = [f"{len(self.selection)} selected"]
        for entity in self.geometry["entities"]:
            if (entity["dimension"], entity["tag"]) in self.selection:
                kind = "Face" if entity["dimension"] == 2 else "Edge"
                lines.append(f"\n{kind} {entity['tag']}\nType: {entity['type']}\nGroups: {', '.join(map(str, entity['groups'])) or 'None'}")
        for region in self.boundary_regions:
            if self.selection & {tuple(e) for e in region['entities']}:
                lines.append('Boundary region: ' + region['label'])
        self.properties.setText("\n".join(lines))

    def retry_pending_close(self):
        if self._close_requested and not self._handling_close and not self.saving_project and self.project_io is None:
            self.close()

    def closeEvent(self, event):
        if self._handling_close:
            event.ignore()
            return
        if self.saving_project or self.project_io is not None:
            self._close_requested = True
            self.statusBar().showMessage('Closing after the current project file operation finishes…')
            event.ignore()
            return
        self._close_requested = False
        self._handling_close = True
        try:
            if self.boundary_dialog is not None:
                self.boundary_dialog.reject()
            if self.parametrisation_dialog is not None:
                self.parametrisation_dialog.reject()
            if not self.confirm_unsaved_changes():
                event.ignore()
                return
            self.recovery_timer.stop()
            self.clear_recovery()
            self.sampling.shutdown()
            if self.import_process:
                process = self.import_process
                self.import_process = None
                process.kill()
                process.waitForFinished(2000)

            if self.project_cache is not None:
                self.project_cache.cleanup()
                self.project_cache = None
            self.settings.setValue("geometry", self.saveGeometry())
            self.settings.setValue("layout", self.saveState())
            super().closeEvent(event)
        finally:
            self._handling_close = False


def main():
    parser = argparse.ArgumentParser(description="Jorvik desktop application")
    parser.add_argument("document", nargs="?", help="Jorvik .jorvik project (legacy .gsproj accepted), Gmsh, STEP, Open CASCADE BREP or IGES file to open")
    parser.add_argument("--version", action="version", version=f"Jorvik {__version__}")
    parser.add_argument("--software-rendering", action="store_true", help="Use software rendering for the 3D viewport")
    args = parser.parse_args()
    document = None
    if args.document:
        from .cli import launch_document
        try:
            document = launch_document(args.document)
        except ValueError as error:
            parser.error(str(error))
    configure_graphics("software" if args.software_rendering else "auto")
    from .shutdown import SignalShutdown
    shutdown = SignalShutdown()
    code = None
    try:
        application = QApplication([sys.argv[0]])
        application.setApplicationName("Jorvik")
        application.setOrganizationName("Jorvik")
        shutdown.start(application)
        settings = QSettings("Jorvik", "Jorvik")
        configure_theme(application, settings.value("theme", "light"))
        install_window_controls(application)
        window = MainWindow(settings)
        shutdown.window = window
        window.show()
        if document and document[0] == 'project':
            try:
                window.load_project_file(document[1], raise_errors=True)
            except Exception as error:
                print(f'Could not open project {document[1]}: {error}', file=sys.stderr)
                code = 1
                window.close()
        elif document:
            def geometry_launch_failed(message):
                print(f'Could not open geometry {document[1]}: {message}', file=sys.stderr)
                QTimer.singleShot(0, lambda: application.exit(1))
            window.load_geometry(document[1], on_error=geometry_launch_failed)
        if code is None:
            code = application.exec()
    finally:
        shutdown.restore(terminating=code is not None)
    sys.exit(code)
