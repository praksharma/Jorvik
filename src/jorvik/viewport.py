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

SCALAR_FIELD_MAP = {
    'distance': ('boundary_distances', 'Boundary distance d(x)'),
    'weight': ('weights', 'Quadrature weight w'),
    'adf': ('adf_distances', 'R-function ADF d̃(x)'),
    'mean_curvature': ('mean_curvature', 'Mean curvature H'),
    'gaussian_curvature': ('gaussian_curvature', 'Gaussian curvature K'),
    'time': ('times', 'Space-time timestamp t'),
    'tag': ('entity_tags', 'CAD entity tag'),
}


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

    @Slot(str, int, int, float, float, float)
    def hovered(self, document, dimension, tag, x, y, z):
        if self.view.geometry and document == self.view.geometry["document"]:
            self.view.hovered.emit(dimension, tag, x, y, z)

    @Slot(str, str, int, float, float, float)
    def hoveredPoint(self, document, label, index, x, y, z):
        if self.view.geometry and document == self.view.geometry["document"]:
            self.view.pointHovered.emit(label, index, x, y, z)

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
    hovered = Signal(int, int, float, float, float)
    pointHovered = Signal(str, int, float, float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.is_ready = False
        self.geometry = None
        self.samples = {
            "batches": [], "visible": True, "through": True, "size": 7,
            "colorBy": "dataset", "colormap": "viridis", "vectorMode": "off", "glyphScale": 1.0,
        }
        self.translation_preview = None
        self.boundary_regions = []
        self.camera_state = None
        self.selection = []
        self.mode = "face"
        self.show_grid = False
        self.show_axes = True
        self.shading_mode = "opaque"
        self.projection_mode = "perspective"
        self.show_bounding_box = False
        self.clipping_plane = {"axis": "off", "position": 0.0, "flip": False, "clipSamples": False}
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

    def set_shading_mode(self, mode):
        self.shading_mode = mode if mode in ("opaque", "translucent", "wireframe") else "opaque"
        self.call("setShadingMode", self.shading_mode)

    def set_projection_mode(self, mode):
        self.projection_mode = "orthographic" if mode == "orthographic" else "perspective"
        self.call("setProjectionMode", self.projection_mode)

    def set_bounding_box_visible(self, visible):
        self.show_bounding_box = bool(visible)
        self.call("setBoundingBoxVisible", self.show_bounding_box)

    def set_clipping_plane(self, config):
        self.clipping_plane.update(config)
        self.call("setClippingPlane", self.clipping_plane)

    def set_geometry(self, geometry):
        self.geometry = geometry
        self.selection = []
        self.flush_geometry()

    @staticmethod
    def _js_samples(samples):
        if not isinstance(samples, dict) or "batches" not in samples:
            return samples
        color_by = samples.get("colorBy", "dataset")
        vector_mode = samples.get("vectorMode", "off")
        field_info = SCALAR_FIELD_MAP.get(color_by)
        slim_batches = []
        for batch in samples["batches"]:
            coords = batch["coordinates"]
            if not isinstance(coords, list):
                coords = coords.tolist()
            item = {"label": batch["label"], "coordinates": coords}
            if field_info is not None:
                field_key, _ = field_info
                vals = batch.get(field_key)
                if vals is not None:
                    item["scalars"] = vals.tolist() if hasattr(vals, "tolist") else list(vals)
            if vector_mode in ("normals", "darboux"):
                for vkey in ("normals", "partner_normals") + (("tangent_u", "tangent_v") if vector_mode == "darboux" else ()):
                    vecs = batch.get(vkey)
                    if vecs is not None:
                        item[vkey] = vecs.tolist() if hasattr(vecs, "tolist") else list(vecs)
            slim_batches.append(item)
        out = {**samples, "batches": slim_batches}
        if field_info is not None:
            out["scalarFieldLabel"] = field_info[1]
        return out

    def flush_geometry(self):
        self.call("loadGeometry", self.geometry)
        self.call("setBoundaryRegions", self.boundary_regions)
        self.call("setMode", self.mode)
        self.call("setSelection", self.selection)
        self.call("setProjectionMode", self.projection_mode)
        self.call("setBoundingBoxVisible", self.show_bounding_box)
        self.call("setShadingMode", self.shading_mode)
        self.call("setClippingPlane", self.clipping_plane)
        self.call("setSamples", self._js_samples(self.samples))
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
        self.samples = {**self.samples, **samples}
        self.call("setSamples", self._js_samples(self.samples))

    def set_sample_style(self, style):
        prev_color = self.samples.get("colorBy", "dataset")
        prev_vec = self.samples.get("vectorMode", "off")
        self.samples.update(style)
        new_color = self.samples.get("colorBy", "dataset")
        new_vec = self.samples.get("vectorMode", "off")
        if (new_color != prev_color or new_vec != prev_vec) and self.samples.get("batches"):
            self.call("setSamples", self._js_samples(self.samples))
        else:
            if new_color in SCALAR_FIELD_MAP:
                style = {**style, "scalarFieldLabel": SCALAR_FIELD_MAP[new_color][1]}
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
