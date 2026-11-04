"""Offline Qt WebEngine viewport with a small application bridge."""
import json
from pathlib import Path
from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from .theme import colors
from .parametrisation import preview_payload


class Bridge(QObject):
    def __init__(self, view):
        super().__init__(view)
        self.view = view

    @Slot()
    def ready(self):
        self.view.is_ready = True
        self.view.startup_timer.stop()
        self.view.apply_theme()
        self.view.flush_geometry()
        self.view.status.emit("Ready" if self.view.geometry else "Ready | Empty geometry workspace")

    @Slot(str, int, int, bool)
    def picked(self, document, dimension, tag, extend):
        if self.view.geometry and document == self.view.geometry["document"]:
            self.view.picked.emit(dimension, tag, extend)

    @Slot(str)
    def failed(self, message):
        self.view.fail(message)

    @Slot(int)
    def orientation(self, index):
        self.view.oriented.emit(index)


class Viewport(QWidget):
    status = Signal(str)
    oriented = Signal(int)
    picked = Signal(int, int, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.is_ready = False
        self.geometry = None
        self.samples = {"batches": [], "visible": True, "through": True, "size": 7}
        self.translation_preview = None
        self.boundary_regions = []
        self.camera_state = None
        self.selection = []
        self.mode = "face"
        self.show_grid = False
        self.show_axes = True
        self.geometry_visibility = {"geometry":True,"faces":True,"edges":True,"isolatedEntities":None,"hiddenFaces":[],"isolatedFaces":None}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)
        self.web = QWebEngineView(self)
        self.web.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        self.web.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        self.channel = QWebChannel(self.web.page())
        self.bridge = Bridge(self)
        self.channel.registerObject("bridge", self.bridge)
        self.web.page().setWebChannel(self.channel)
        self.web.page().renderProcessTerminated.connect(lambda status, code: self.fail(f"Renderer stopped (code {code})."))
        self.web.loadFinished.connect(lambda ok: None if ok else self.fail("Could not load the local viewport."))
        layout.addWidget(self.web)
        self.startup_timer = QTimer(self)
        self.startup_timer.setSingleShot(True)
        self.startup_timer.timeout.connect(lambda: self.fail("Renderer did not become ready."))
        self.startup_timer.start(20000)
        self.web.setUrl(QUrl.fromLocalFile(str(Path(__file__).parent / "viewport3d/index.html")))

    def fail(self, message):
        self.startup_timer.stop()
        self.is_ready = False
        self.error.setText("3D graphics unavailable: " + str(message)[:600] + "\nTry restarting with --software-rendering.")
        self.error.show()
        self.web.hide()
        self.status.emit("3D graphics unavailable")

    def call(self, method, value=None):
        if self.is_ready:
            args = "" if value is None else json.dumps(value, allow_nan=False)
            self.web.page().runJavaScript(f"window.geoViewer.{method}({args});")

    def apply_theme(self):
        self.call("appearance", {**{k: colors()[k] for k in ("canvas", "grid")},
                                 "showGrid": self.show_grid, "showAxes": self.show_axes})

    def set_geometry_visibility(self, visibility):
        self.geometry_visibility.update(visibility)
        self.call("setGeometryVisibility", self.geometry_visibility)

    def set_geometry(self, geometry):
        self.geometry = geometry
        self.selection = []
        self.flush_geometry()

    def flush_geometry(self):
        self.call("loadGeometry", self.geometry)
        self.call("setBoundaryRegions", self.boundary_regions)
        self.call("setMode", self.mode)
        self.call("setSelection", self.selection)
        self.call("setSamples", self.samples)
        self.call("setGeometryVisibility",self.geometry_visibility)
        self.call("setTranslationPreview", preview_payload(self.translation_preview))
        if self.camera_state:
            self.call("setCameraState",self.camera_state)

    def set_mode(self, mode):
        self.mode = mode
        self.call("setMode", mode)

    def set_selection(self, selection):
        self.selection = selection
        self.call("setSelection", selection)

    def set_samples(self, samples):
        self.samples = samples
        self.call("setSamples", samples)

    def set_sample_style(self, style):
        self.samples.update(style)
        self.call("setSampleStyle", style)

    def set_boundary_regions(self, regions):
        self.boundary_regions = regions
        self.call('setBoundaryRegions', regions)

    def set_translation_preview(self, setup):
        payload = preview_payload(setup)
        self.translation_preview = setup
        self.call('setTranslationPreview', payload)

    def set_camera_state(self, state):
        self.camera_state = state
        self.call('setCameraState', state)
