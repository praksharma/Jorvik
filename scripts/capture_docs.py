"""Capture real GUI documentation images using an isolated example session.

Run from a checkout with a desktop session: uv run python scripts/capture_docs.py
No user projects or settings are opened. The example sweep lives in a temporary
folder. Images are written to docs/assets/screenshots.
"""
from pathlib import Path
import tempfile
import time

from PySide6.QtCore import QSettings, QPoint
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
            from jorvik.area_editor import AreaEditor
            area_editor = AreaEditor(window, 'inner')
            area_editor.method.setCurrentIndex(area_editor.method.findData('lhs'))
            area_editor.count.setValue(256)
            area_editor.seed.setValue(99)
            area_editor.resolution.setCurrentIndex(area_editor.resolution.findData(64))
            area_editor.show()
            capture(area_editor, 'area-editor-dialog')
            area_editor.reject()
            area_editor.deleteLater()
            original_controls = panel.recipe_control_state()
            panel.target.setCurrentIndex(panel.target.findText('Region: inner'))
            panel.count.setValue(64); panel.label.setText('inner')
            panel.compute_mode.setCurrentIndex(panel.compute_mode.findData('reproducible'))
            from jorvik.sampling_recipe import create_recipe, save_recipe, load_recipe
            recipe_path = Path(directory) / 'inner.sampling.json'
            save_recipe(create_recipe(panel.build_request()), recipe_path)
            panel.count.setValue(13); panel.label.setText('temporary')
            panel.apply_sampling_recipe(load_recipe(recipe_path))
            if panel.count.value() != 64 or len(panel.batches) != 2:
                raise RuntimeError('Sampling recipe did not restore controls without generation.')
            panel.scroll.verticalScrollBar().setValue(0)
            capture(window, 'sampling-recipe')
            panel.restore_recipe_controls(original_controls)
            menu = window.menuBar().actions()[0].menu()
            menu.popup(window.menuBar().mapToGlobal(window.menuBar().actionGeometry(window.menuBar().actions()[0]).bottomLeft()))
            capture(menu, 'file-menu')
            menu.close()
            from unittest.mock import patch
            lightweight_path = Path(directory) / 'annulus-recipes.jorvik'
            with patch('jorvik.project_ui.QFileDialog.getSaveFileName', return_value=(str(lightweight_path), '')):
                if not window.save_lightweight_copy(): raise RuntimeError('Could not save lightweight example.')
            lightweight = MainWindow(QSettings(str(Path(directory) / 'light-settings.ini'), QSettings.Format.IniFormat))
            lightweight.resize(1440, 940); lightweight.show()
            try:
                wait(lambda: lightweight.view.is_ready)
                lightweight.load_project_file(lightweight_path, raise_errors=True)
                if lightweight.sampling.batches: raise RuntimeError('Lightweight copy unexpectedly contains points.')
                lightweight.view.call('orient', 1)
                capture(lightweight, 'lightweight-project')
                lightweight.sampled_areas.setCurrentItem(lightweight.sampled_areas.items['inner'])
                menu = lightweight.area_menu('inner')
                menu.popup(lightweight.sampled_areas.mapToGlobal(QPoint(80, 80)))
                capture(menu, 'regenerate-recipe-menu'); menu.close()
                lightweight.regenerate_area('inner')
                wait(lambda: lightweight.sampling.process is None)
                if len(lightweight.sampling.batches) != 1: raise RuntimeError('Saved recipe did not regenerate.')
                lightweight.sampling_dock.raise_()
                capture(lightweight, 'lightweight-regenerated')
            finally:
                lightweight.confirm_unsaved_changes = lambda: True
                lightweight.close()
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
            dialog.scope.setCurrentIndex(dialog.scope.findData('regions'))
            for i in range(dialog.regions.count()):
                item = dialog.regions.item(i)
                item.setCheckState(
                    dialog.regions.item(i).checkState().__class__.Checked
                    if item.text() == 'inner'
                    else dialog.regions.item(i).checkState().__class__.Unchecked
                )
            dialog.offsets[0].setValue(0.35)
            dialog.preview()
            dialog.hide()
            capture(window, 'region-augmentation-preview')
            dialog.show()
            dialog.scope.setCurrentIndex(dialog.scope.findData('domain'))
            dialog.offsets[0].setValue(0.0)
            dialog.restore()
            lhs_design = {
                'schema_version': 1, 'method': 'lhs', 'seed': 42, 'count': 12,
                'parameters': [
                    {'name': 'scale_x', 'target': 'scale_x', 'mode': 'range',
                     'start': 0.8, 'stop': 1.6, 'reference': 1.0, 'unit': 'dimensionless'},
                    {'name': 'scale_y', 'target': 'scale_y', 'mode': 'range',
                     'start': 1.0, 'stop': 1.8, 'reference': 1.0, 'unit': 'dimensionless'},
                    {'name': 'angle', 'target': 'rotation_angle', 'mode': 'range',
                     'start': -30.0, 'stop': 30.0, 'reference': 0.0, 'unit': 'degrees'},
                ],
                'constraints': [
                    {'name': 'aspect_order', 'left': 'scale_x', 'operator': '<=',
                     'right': {'parameter': 'scale_y'}},
                ],
            }
            dialog.design_panel.apply_design(lhs_design)
            dialog.design_panel.generate()
            dialog.tabs.setCurrentIndex(1)
            dialog.tabs.currentWidget().verticalScrollBar().setValue(0)
            capture(dialog, 'lhs-parameter-design')
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
            from jorvik.sweep_export import export_sweep
            base, records = dialog.sweep_snapshot()
            stopped = []
            previous = Path(directory) / 'interrupted-sweep'
            export_sweep(tuple(window.sampling.batches), base, records, previous,
                         cancelled=lambda: bool(stopped),
                         progress=lambda data: stopped.append(True) if data.get('succeeded_count') else None)
            dialog.sweep_panel.start_resume(previous, Path(directory) / 'continued-sweep')
            wait(lambda: not dialog.sweep_panel.running)
            if dialog.sweep_panel.last_error or dialog.sweep_panel.last_result['reused_count'] != 1:
                raise RuntimeError('Example sweep did not resume correctly.')
            capture(dialog, 'sweep-resume')
            from copy import deepcopy
            constrained = deepcopy(design)
            constrained['constraints'] = [{'name': 'limit_angle', 'left': 'angle', 'operator': '<=',
                                           'right': {'value': 30.}}]
            dialog.design_panel.apply_design(constrained)
            dialog.design_panel.generate()
            if len(dialog.design_panel._records) != 4 or len(dialog.design_panel._rejected) != 2:
                raise RuntimeError('Example constraints did not produce the expected report.')
            dialog.tabs.setCurrentIndex(1)
            dialog.tabs.currentWidget().ensureWidgetVisible(dialog.design_panel.constraints)
            capture(dialog, 'parameter-constraints')
            dialog.design_panel.rejected_button.click()
            from jorvik.constraint_panel import RejectedConfigurationsDialog
            rejections = dialog.design_panel.findChild(RejectedConfigurationsDialog)
            capture(rejections, 'rejected-configurations')
            rejections.close()
            dialog.tabs.setCurrentIndex(2)
            dialog.sweep_panel.start_export(Path(directory) / 'constrained-sweep')
            wait(lambda: not dialog.sweep_panel.running)
            if dialog.sweep_panel.last_error: raise RuntimeError(dialog.sweep_panel.last_error)
            dialog.close()
            wait(lambda: window.parametrisation_dialog is None)
            from jorvik.sweep_inspector import SweepInspector
            inspector = SweepInspector(Path(directory) / 'constrained-sweep', window)
            window.sweep_inspector = inspector
            inspector.finished.connect(window.finish_sweep_inspection)
            inspector.show()
            wait(lambda: not inspector.running)
            inspector.verify_button.click()
            wait(lambda: not inspector.running)
            if len(inspector.verification) != 4:
                raise RuntimeError('Example sweep verification did not complete.')
            capture(inspector, 'sweep-inspector')
            inspector.close()
            wait(lambda: window.sweep_inspector is None)

            # Method comparison on the annulus (Grid vs Sobol)
            panel.target.setCurrentIndex(panel.target.findData('interior'))
            panel.method.setCurrentIndex(panel.method.findData('grid'))
            panel.count.setValue(256)
            panel.label.setText('grid_interior')
            panel.start(); wait(lambda: panel.process is None)
            window.sampled_areas.items['interior'].setCheckState(
                0, window.sampled_areas.items['interior'].checkState(0).__class__.Unchecked)
            panel.point_size.setValue(9)
            window.sampling_dock.raise_()
            capture(window, 'method-comparison-grid-vs-sobol')
            panel.point_size.setValue(7)

            # 3D sphere tutorial captures: face selection, face context menu, cutaway interior, isolation, and dark theme
            window.confirm_unsaved_changes = lambda *args, **kwargs: True
            window.load_geometry(ROOT / 'examples/geometries/cfdbench/sphere_unstructured.geo')
            wait(lambda: window.import_process is None)
            window.view.call('orient', 0)
            window.set_selection_mode('face')
            for tag in (1, 2, 3):
                window.pick_entity(2, tag, tag > 1)
            window.properties_dock.raise_()
            capture(window, 'sphere-faces-selected')
            face_menu = window.face_menu()
            face_menu.popup(window.tree.mapToGlobal(QPoint(90, 110)))
            capture(face_menu, 'sphere-face-context-menu')
            face_menu.close()
            window.hide_faces([1, 2, 3])
            window.clear_selection()
            for tag in (4, 5, 6, 7, 8):
                window.pick_entity(2, tag, tag > 4)
            window.sampling_dock.raise_()
            panel.target.setCurrentIndex(panel.target.findData('selection'))
            panel.method.setCurrentIndex(panel.method.findData('sobol'))
            panel.count.setValue(256)
            panel.label.setText('back_shell')
            panel.start(); wait(lambda: panel.process is None)
            window.clear_selection()
            panel.target.setCurrentIndex(panel.target.findData('interior'))
            panel.method.setCurrentIndex(panel.method.findData('hammersley'))
            panel.count.setValue(512)
            panel.label.setText('volume_interior')
            panel.start(); wait(lambda: panel.process is None)
            window.sampling_dock.raise_()
            capture(window, 'sphere-hidden-faces-interior')
            window.show_sampled_area('back_shell')
            window.isolate_geometry_action.setChecked(True)
            window.isolate_action.setChecked(True)
            window.sampling_dock.raise_()
            capture(window, 'region-isolation-context')
            window.isolate_action.setChecked(False)
            window.isolate_geometry_action.setChecked(False)
            window.show_all_faces()
            window.set_theme('dark')
            panel.point_size.setValue(8)
            window.sampling_dock.raise_()
            capture(window, 'dark-theme-workspace')
            window.set_theme('light')
            panel.point_size.setValue(7)

            # 3D Open CASCADE BREP cylinder tutorial capture with verified CAD containment normals
            window.load_geometry(ROOT / 'examples/geometries/cad/cylinder.brep')
            wait(lambda: window.import_process is None)
            window.view.call('orient', 0)
            window.set_selection_mode('face')
            window.pick_entity(2, 1, False)
            window.open_boundary_regions()
            reg_dialog = window.boundary_dialog
            reg_dialog.label.setText('curved_wall')
            reg_dialog.save_region()
            reg_dialog.close()
            wait(lambda: window.boundary_dialog is None)
            window.clear_selection()
            panel.target.setCurrentIndex(panel.target.findText('Region: curved_wall'))
            panel.method.setCurrentIndex(panel.method.findData('halton'))
            panel.count.setValue(192)
            panel.label.setText('curved_wall')
            panel.start(); wait(lambda: panel.process is None)
            panel.target.setCurrentIndex(panel.target.findData('interior'))
            panel.method.setCurrentIndex(panel.method.findData('stratified'))
            panel.count.setValue(256)
            panel.label.setText('cylinder_interior')
            panel.start(); wait(lambda: panel.process is None)
            window.show_sampled_area('curved_wall')
            capture(window, 'brep-cylinder-normals')

            # 3D STEP solid tutorial capture
            window.load_geometry(ROOT / 'examples/geometries/cad/t20_data.step')
            wait(lambda: window.import_process is None)
            window.view.call('orient', 0)
            window.set_selection_mode('face')
            window.pick_entity(2, 1, False)
            window.pick_entity(2, 2, True)
            panel.target.setCurrentIndex(panel.target.findData('selection'))
            panel.method.setCurrentIndex(panel.method.findData('lhs'))
            panel.count.setValue(160)
            panel.label.setText('step_faces')
            panel.start(); wait(lambda: panel.process is None)
            window.clear_selection()
            panel.target.setCurrentIndex(panel.target.findData('interior'))
            panel.method.setCurrentIndex(panel.method.findData('sobol'))
            panel.count.setValue(320)
            panel.label.setText('step_interior')
            panel.start(); wait(lambda: panel.process is None)
            window.sampling_dock.raise_()
            capture(window, 'step-solid-imported')

            # Open surface STEP shell with degenerate edges and unavailable outward normals
            window.load_geometry(ROOT / 'examples/geometries/cad/cube_filet.stp')
            wait(lambda: window.import_process is None)
            window.view.call('orient', 0)
            window.set_selection_mode('face')
            window.pick_entity(2, 1, False)
            panel.target.setCurrentIndex(panel.target.findData('selection'))
            panel.method.setCurrentIndex(panel.method.findData('random'))
            panel.count.setValue(128)
            panel.label.setText('fillet_shell')
            panel.start(); wait(lambda: panel.process is None)
            window.show_sampled_area('fillet_shell')
            capture(window, 'open-shell-unavailable-normals')
        finally:
            # This is a disposable example session, so never prompt to save it.
            window.confirm_unsaved_changes = lambda *args, **kwargs: True
            window.close()
            app.processEvents()


if __name__ == '__main__':
    main()
