"""Metadata-only configuration designs, stale selection and bounded UI pages."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QSignalSpy
from jorvik.parameter_design_panel import ParameterDesignPanel
from test_export import batch
from test_affine_transforms import affine_setup
import test_shell


def parameter(name='offset', target='translation_x', **changes):
    value = {'name': name, 'target': target, 'mode': 'range', 'start': 0., 'stop': 2.,
             'count': 3, 'reference': 0., 'unit': 'source'}
    value.update(changes)
    return value


def design(**changes):
    value = {'schema_version': 1, 'method': 'cartesian', 'seed': 42, 'count': 16,
             'parameters': [parameter()]}
    value.update(changes)
    return value


class ParameterDesignUiTest(unittest.TestCase):
    setUpClass = classmethod(test_shell.ShellTest.setUpClass.__func__)

    def panel(self):
        base = affine_setup()
        batches = [batch('wall', 3), batch('interior', 5)]
        window = SimpleNamespace(sampling=SimpleNamespace(batches=batches, start=lambda: None))
        panel = ParameterDesignPanel(window, lambda: deepcopy(base))
        def cleanup():
            panel.close(); panel.deleteLater()
            self.app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.addCleanup(cleanup)
        return panel, base, batches

    def test_explicit_generation_selection_and_stale_edits(self):
        panel, base, batches = self.panel()
        originals = deepcopy(batches)
        self.assertIsNone(panel.design())
        self.assertEqual(panel.selected_setup(base), base)
        self.assertFalse(panel.generate_button.isEnabled())
        panel.apply_design(design())
        self.assertEqual(panel.design(), design())
        self.assertEqual(panel.configurations.rowCount(), 0)
        self.assertIn('3 configurations', panel.summary.text())
        self.assertIn('8 points each', panel.summary.text())
        self.assertIn('24 points', panel.summary.text())
        with self.assertRaisesRegex(ValueError, 'stale'): panel.selected_setup(base)
        preview = QSignalSpy(panel.requestPreview); export = QSignalSpy(panel.requestExport)
        with patch.object(panel.window.sampling, 'start') as sampling:
            panel.generate_button.click()
            sampling.assert_not_called()
        self.assertEqual(panel.configurations.rowCount(), 3)
        self.assertTrue(panel.preview_button.isEnabled())
        panel.configurations.setCurrentCell(2, 0)
        selected = panel.selected_setup(base)
        self.assertEqual(selected['translation'], [2., 0., 0.])
        self.assertEqual(selected['design_configuration']['id'], 'configuration_000003')
        self.assertEqual(preview.count(), 0); self.assertEqual(export.count(), 0)
        panel.preview_button.click(); panel.export_button.click()
        self.assertEqual(preview.count(), 1); self.assertEqual(export.count(), 1)
        self.assertEqual(batches, originals)
        selected['translation'][0] = 99
        self.assertEqual(panel.selected_setup(base)['translation'][0], 2.)
        changed = QSignalSpy(panel.changed)
        panel.parameters.item(0, 4).setText('3')
        self.assertGreater(changed.count(), 0)
        self.assertFalse(panel.preview_button.isEnabled())
        self.assertEqual(panel.configurations.rowCount(), 0)
        with self.assertRaisesRegex(ValueError, 'stale'): panel.selected_setup(base)
        panel.generate()
        base['pivot'][0] = 1
        with self.assertRaisesRegex(ValueError, 'stale'): panel.selected_setup(base)
        panel.invalidate()
        self.assertFalse(panel.export_button.isEnabled())

    def test_pagination_limits_and_atomic_loading(self):
        panel, base, _ = self.panel()
        config = design(parameters=[parameter(count=450, stop=449)])
        panel.apply_design(config); panel.generate()
        self.assertEqual(len(panel._records), 450)
        self.assertEqual(panel.configurations.rowCount(), 200)
        self.assertEqual(panel.page.maximum(), 3)
        panel.page.setValue(3)
        self.assertEqual(panel.configurations.rowCount(), 50)
        self.assertEqual(panel.selected_setup(base)['design_configuration']['id'], 'configuration_000401')
        panel.configurations.setCurrentCell(49, 0)
        self.assertEqual(panel.selected_setup(base)['translation'][0], 449.)
        before = panel.design(); selected = panel.selected_setup(base)
        for bad in (design(parameters=[]), design(seed=-1),
                    design(parameters=[parameter(count=10000), parameter('y', 'translation_y', count=2)]),
                    design(parameters=[parameter(unit='mm')]),
                    design(parameters=[parameter(), parameter('again', 'translation_x')])):
            with self.subTest(bad=bad), self.assertRaises(ValueError): panel.apply_design(bad)
            self.assertEqual(panel.design(), before)
            self.assertEqual(panel.selected_setup(base), selected)
        # A huge product is rejected before any table or point allocation.
        panel.apply_design(design(parameters=[parameter(count=100)]))
        panel.add_parameter()
        panel.parameters.item(1, 5).setText('101')
        self.assertIn('10000', panel.summary.text())
        with patch('jorvik.parameter_design_panel.QMessageBox.warning') as warning:
            panel.generate(); warning.assert_called_once()
        self.assertEqual(panel.configurations.rowCount(), 0)
        panel.apply_design(None)
        self.assertIsNone(panel.design())
        self.assertEqual(panel.selected_setup(base), base)

    def test_seeded_designs_and_readonly_units(self):
        panel, base, _ = self.panel()
        for method in ('random', 'lhs', 'sobol'):
            config = design(method=method, count=8, seed=4294967295,
                            parameters=[parameter('turn', 'rotation_angle', unit='degrees')])
            panel.apply_design(config)
            self.assertTrue(panel.seed.isEnabled()); self.assertTrue(panel.count.isEnabled())
            panel.generate(); first = deepcopy(panel._records)
            panel.generate(); self.assertEqual(panel._records, first)
            self.assertEqual(panel.design()['seed'], 4294967295)
        panel.apply_design(design())
        target = panel.parameters.cellWidget(0, 1)
        target.setCurrentIndex(target.findData('scale_x'))
        self.assertEqual(panel.parameters.item(0, 8).text(), 'dimensionless')
        # Scale references cannot be zero, so invalid settings remain a visible draft.
        self.assertIn('needs attention', panel.summary.text())
        self.assertFalse(panel.preview_button.isEnabled())
