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

    def test_search_preserves_ids_values_pages_and_full_export_records(self):
        panel, base, _ = self.panel()
        config = design(parameters=[parameter('Δ offset [source]', count=450, stop=449)])
        panel.apply_design(config); panel.generate()
        fingerprint = panel._fingerprint
        declared = panel.design()
        records = panel.generated_records(base)
        self.assertEqual(len(records), 450)
        self.assertIs(records[0], panel._records[0])
        changed = QSignalSpy(panel.changed)
        preview = QSignalSpy(panel.requestPreview)
        with patch('jorvik.parameter_design.generate_configurations') as generate:
            panel.search_edit.setText('CONFIGURATION_000401')
            self.assertEqual(panel.configurations.rowCount(), 1)
            self.assertEqual(panel.selected_setup(base)['design_configuration']['id'], 'configuration_000401')
            self.assertEqual(panel.selected_setup(base)['translation'][0], 400.)
            self.assertTrue(panel.preview_button.isEnabled())
            self.assertIn('1 matches', panel.page_info.text())
            # Full-precision numeric text is searchable even though display uses .10g.
            panel.search_edit.setText('449.0')
            self.assertEqual(panel.configurations.rowCount(), 1)
            self.assertEqual(panel.selected_setup(base)['design_configuration']['id'], 'configuration_000450')
            self.assertEqual(panel.configurations.item(0, 1).text(), '449')
            # A literal bracket and Unicode name do not invoke a regular expression.
            panel.search_edit.setText('δ OFFSET [source]')
            self.assertEqual(panel.configurations.rowCount(), 200)
            self.assertEqual(panel.page.maximum(), 3)
            panel.page.setValue(3)
            self.assertEqual(panel.configurations.rowCount(), 50)
            panel.configurations.setCurrentCell(49, 0)
            self.assertEqual(panel.selected_setup(base)['translation'][0], 449.)
            panel.search_edit.setText('no such configuration')
            self.assertEqual(panel.configurations.rowCount(), 0)
            self.assertFalse(panel.preview_button.isEnabled())
            self.assertFalse(panel.export_button.isEnabled())
            self.assertFalse(panel.page.isEnabled())
            self.assertIn('0 matches', panel.page_info.text())
            with self.assertRaisesRegex(ValueError, 'Select a configuration'):
                panel.selected_setup(base)
            # Search only changes the view: an all-design export still has every row.
            self.assertEqual(panel.generated_records(base), records)
            panel.search_edit.clear()
            self.assertEqual(panel.page.value(), 1)
            self.assertEqual(panel.page.maximum(), 3)
            self.assertEqual(panel.configurations.rowCount(), 200)
            self.assertEqual(panel.selected_setup(base)['design_configuration']['id'], 'configuration_000001')
            generate.assert_not_called()
        self.assertEqual(changed.count(), 0)
        self.assertEqual(preview.count(), 0)
        self.assertEqual(panel._fingerprint, fingerprint)
        self.assertEqual(panel.design(), declared)
        panel.parameters.item(0, 4).setText('450')
        with self.assertRaisesRegex(ValueError, 'stale'): panel.generated_records(base)
        panel.apply_design(None)
        with self.assertRaisesRegex(ValueError, 'Enable a parameter design'):
            panel.generated_records(base)

    def test_filtered_pagination_maps_back_to_original_records(self):
        panel, base, _ = self.panel()
        # Matching every name keeps more than one page while still exercising the
        # index-map path; ID filtering then moves to a nonzero original offset.
        panel.apply_design(design(parameters=[parameter(count=450, stop=449)]))
        panel.generate(); panel.search_edit.setText('configuration_0004')
        self.assertEqual(panel.configurations.rowCount(), 51)
        self.assertEqual(panel.selected_setup(base)['translation'][0], 399.)
        self.assertEqual(panel.selected_setup(base)['design_configuration']['id'], 'configuration_000400')
        panel.configurations.setCurrentCell(50, 0)
        self.assertEqual(panel.selected_setup(base)['translation'][0], 449.)
        self.assertEqual(len(panel.generated_records(base)), 450)
        base['pivot'][0] = 2.
        with self.assertRaisesRegex(ValueError, 'stale'): panel.generated_records(base)
        base['pivot'][0] = 0.
        panel.apply_design(design(parameters=[parameter(count=1000, stop=999)]))
        panel.generate(); panel.search_edit.setText('2')
        matching_values = [value for value in range(1000) if '2' in str(value) or '2' in str(value + 1)]
        self.assertGreater(len(matching_values), panel.PAGE_SIZE)
        panel.page.setValue(panel.page.maximum())
        panel.configurations.setCurrentCell(panel.configurations.rowCount() - 1, 0)
        selected = panel.selected_setup(base)
        self.assertEqual(selected['translation'][0], matching_values[-1])
        self.assertEqual(selected['design_configuration']['id'], f'configuration_{matching_values[-1] + 1:06d}')
