"""Capture real GUI documentation images using an isolated example session.

Run from a checkout with a desktop session: uv run python scripts/capture_docs.py
No user projects or settings are opened. The example sweep lives in a temporary
folder. Images are written to docs/assets/screenshots.
"""
from pathlib import Path
import tempfile
import time

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from jorvik.graphics import configure_graphics
from jorvik.theme import configure_theme
from jorvik.app import MainWindow

ROOT = Path(__file__).resolve().parents[1]


def main():
    configure_graphics('software')
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    configure_theme(app, 'light')
    output = ROOT / 'docs/assets/screenshots'
    output.mkdir(parents=True, exist_ok=True)

    def wait(predicate, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate(): return
            time.sleep(.01)
        raise RuntimeError('Timed out preparing the documentation screenshot.')

    def settle():
        deadline = time.monotonic() + .65
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)

    def capture(widget, name):
        settle()
        image = widget.grab()
        if image.isNull() or not image.save(str(output / (name + '.png'))):
            raise RuntimeError('Could not save screenshot: ' + name)
        print(name, image.size().toTuple(), flush=True)

    with tempfile.TemporaryDirectory(prefix='jorvik-docs-') as directory:
        settings = QSettings(str(Path(directory) / 'settings.ini'), QSettings.Format.IniFormat)
        window = MainWindow(settings)
        window.resize(1440, 940)
        window.show()
        try:
            wait(lambda: window.view.is_ready)
            window.load_geometry(ROOT / 'examples/geometries/cfdbench/annulus_unstructured.geo')
            wait(lambda: window.import_process is None)
            if window.geometry is None: raise RuntimeError('Example geometry did not load.')
            window.view.call('orient', 1)
            window.set_selection_mode('edge')
            for tag in range(1, 5): window.pick_entity(1, tag, tag > 1)
            capture(window, 'workspace')
            window.open_boundary_regions()
            dialog = window.boundary_dialog
            dialog.label.setText('inner')
            capture(dialog, 'boundary-region')
            dialog.save_region()
            dialog.close()
            wait(lambda: window.boundary_dialog is None)
            window.selection.clear(); window.update_selection()
            panel = window.sampling
            panel.method.setCurrentIndex(panel.method.findData('sobol'))
            panel.target.setCurrentIndex(panel.target.findText('Region: inner'))
            panel.count.setValue(128)
            panel.seed.setValue(42)
            panel.label.setText('inner')
            panel.start(); wait(lambda: panel.process is None)
            if len(panel.batches) != 1: raise RuntimeError('Boundary sampling failed.')
            capture(window, 'boundary-samples')
            panel.target.setCurrentIndex(panel.target.findData('interior'))
            panel.count.setValue(512)
            panel.label.setText('interior')
            panel.start(); wait(lambda: panel.process is None)
            if len(panel.batches) != 2: raise RuntimeError('Interior sampling failed.')
            capture(window, 'sampled-workspace')
            menu = window.menuBar().actions()[0].menu()
            menu.popup(window.menuBar().mapToGlobal(window.menuBar().actionGeometry(window.menuBar().actions()[0]).bottomLeft()))
            capture(menu, 'file-menu')
            menu.close()
            window.open_parametrisation()
            dialog = window.parametrisation_dialog
            dialog.resize(940, 780)
            dialog.scales[0].setValue(1.5)
            dialog.rotation_angle.setValue(30)
            capture(dialog, 'transform-parameters')
            dialog.preview()
            dialog.hide()
            capture(window, 'transform-preview')
            dialog.show()
            design = {'schema_version': 1, 'method': 'cartesian', 'seed': 42, 'count': 6,
                      'parameters': [
                          {'name': 'angle', 'target': 'rotation_angle', 'mode': 'values',
                           'values': [0., 30., 60.], 'reference': 0., 'unit': 'degrees'},
                          {'name': 'shift', 'target': 'translation_x', 'mode': 'values',
                           'values': [0., 1.], 'reference': 0., 'unit': 'source'}]}
            dialog.design_panel.apply_design(design)
            dialog.design_panel.generate()
            dialog.tabs.setCurrentIndex(1)
            capture(dialog, 'parameter-design')
            dialog.tabs.currentWidget().verticalScrollBar().setValue(
                dialog.tabs.currentWidget().verticalScrollBar().maximum())
            capture(dialog, 'configuration-list')
            dialog.tabs.setCurrentIndex(2)
            dialog.sweep_panel.start_export(Path(directory) / 'example-sweep')
            wait(lambda: not dialog.sweep_panel.running)
            if dialog.sweep_panel.last_error: raise RuntimeError(dialog.sweep_panel.last_error)
            capture(dialog, 'sweep-export')
            dialog.close()
            wait(lambda: window.parametrisation_dialog is None)
            from jorvik.sweep_inspector import SweepInspector
            inspector = SweepInspector(Path(directory) / 'example-sweep', window)
            window.sweep_inspector = inspector
            inspector.finished.connect(window.finish_sweep_inspection)
            inspector.show()
            wait(lambda: not inspector.running)
            inspector.verify_button.click()
            wait(lambda: not inspector.running)
            if len(inspector.verification) != 6:
                raise RuntimeError('Example sweep verification did not complete.')
            capture(inspector, 'sweep-inspector')
            inspector.close()
        finally:
            # This is a disposable example session, so never prompt to save it.
            window.confirm_unsaved_changes = lambda *args, **kwargs: True
            window.close()
            app.processEvents()


if __name__ == '__main__':
    main()
