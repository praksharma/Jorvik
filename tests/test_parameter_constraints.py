"""Literal constraints, stable IDs, rejection provenance and GUI editing."""
from copy import deepcopy
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from jorvik.parameter_design import generate_design, generate_configurations, validate_design
from jorvik.parametrisation import validate_setup, save_setup, load_setup
from jorvik.sweep_export import export_sweep, resume_sweep
from jorvik.sweep_results import inspect_sweep, iter_sweep_datasets
from test_affine_transforms import affine_setup
from test_parameter_design import parameter, design
from test_export import batch


def rule(left='shift', operator='<=', value=0.5, name='limit'):
    return {'name': name, 'left': left, 'operator': operator, 'right': {'value': value}}


class ConstraintTest(unittest.TestCase):
    def test_rejection_ids_reasons_and_all_operators(self):
        definition=design(constraints=[rule()])
        before=deepcopy(definition)
        report=generate_design(affine_setup(), definition)
        self.assertEqual([r['id'] for r in report['accepted']], ['configuration_000001', 'configuration_000002'])
        self.assertEqual(report['rejected'][0]['id'], 'configuration_000003')
        self.assertIn('limit', report['rejected'][0]['reasons'][0])
        self.assertEqual(definition, before)
        expected={'<':1, '<=':2, '>':1, '>=':2, '==':1, '!=':2}
        for op, count in expected.items():
            self.assertEqual(len(generate_configurations(affine_setup(), design(constraints=[rule(operator=op)]))), count)
        all_rejected=generate_design(affine_setup(), design(constraints=[rule(value=-1)]))
        self.assertEqual(all_rejected['accepted'], []); self.assertEqual(len(all_rejected['rejected']), 3)
        two=design(parameters=[parameter('x'), parameter('y', 'translation_y')],
                   constraints=[{'name':'ordering', 'left':'x', 'operator':'<=', 'right':{'parameter':'y'}}])
        report=generate_design(affine_setup(), two)
        self.assertEqual(len(report['accepted']), 6); self.assertEqual(len(report['rejected']), 3)
        self.assertEqual(report['accepted'][3]['id'], 'configuration_000005')

    def test_stochastic_filtering_is_reproducible_without_replacement(self):
        for method in ('random', 'lhs', 'sobol'):
            plain=design(method=method, count=16)
            original=generate_configurations(affine_setup(), plain)
            constrained=dict(plain, constraints=[rule(value=0.3)])
            report=generate_design(affine_setup(), constrained)
            self.assertEqual(report, generate_design(affine_setup(), constrained))
            expected=[r for r in original if r['parameters']['shift'] <= 0.3]
            self.assertEqual([(r['id'],r['parameters']) for r in report['accepted']],
                             [(r['id'],r['parameters']) for r in expected])
            self.assertEqual(len(report['accepted'])+len(report['rejected']),16)

    def test_invalid_rules_units_and_scalar_provenance(self):
        bad=[None, [rule(left='missing')], [rule(operator='eval')], [rule(value=float('nan'))],
             [rule(),rule()], [dict(rule(),right={'value':0,'parameter':'shift'})], [rule(value=True)]]
        for constraints in bad:
            with self.subTest(constraints=constraints), self.assertRaises(ValueError):
                validate_design(design(constraints=constraints))
        mixed=design(parameters=[parameter(),parameter('angle','rotation_angle')],
                     constraints=[{'name':'units','left':'shift','operator':'<','right':{'parameter':'angle'}}])
        with self.assertRaisesRegex(ValueError,'units'):validate_design(mixed)
        definition=design(constraints=[rule()]); setup=affine_setup(design=definition)
        record=generate_configurations(setup,definition)[0]
        forged=deepcopy(record['setup']); forged['translation'][0]=1.; forged['design_configuration']['parameters']['shift']=1.
        with self.assertRaisesRegex(ValueError,'constraints'):validate_setup(forged)
        attached=deepcopy(record['setup']); attached['design']=definition
        del attached['design_configuration']['constraints']
        with self.assertRaisesRegex(ValueError,'constraints differ'):validate_setup(attached)
        with TemporaryDirectory() as directory:
            path=Path(directory)/'setup.json'; save_setup(setup,path)
            self.assertEqual(load_setup(path),setup)

    def test_sweep_report_integrity_readers_and_resume(self):
        clouds=[batch()]; definition=design(constraints=[rule()]); base=affine_setup(design=definition)
        records=generate_configurations(base,definition)
        with TemporaryDirectory() as directory:
            root=Path(directory)/'run'
            export_sweep(clouds,base,records,root,file_format='npz')
            run=inspect_sweep(root)
            self.assertEqual(len(run['completed']),2); self.assertEqual(len(run['rejected']),1)
            self.assertEqual(len(list(iter_sweep_datasets(root))),2)
            manifest=resume_sweep(clouds,base,records,root,Path(directory)/'resumed')
            self.assertEqual(manifest['reused_count'],2)
            self.assertEqual(manifest['design_filter']['rejected_count'],1)
            original=(root/'rejections.json').read_bytes()
            (root/'rejections.json').write_bytes(original+b' ')
            with self.assertRaisesRegex(ValueError,'checksum'):inspect_sweep(root)


class ConstraintUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import test_shell
        test_shell.ShellTest.setUpClass.__func__(cls)

    def test_editor_rejection_report_staleness_and_all_rejected(self):
        from jorvik.parameter_design_panel import ParameterDesignPanel
        from jorvik.constraint_panel import RejectedConfigurationsDialog
        base=affine_setup()
        panel=ParameterDesignPanel(SimpleNamespace(sampling=SimpleNamespace(batches=[batch()])), lambda:deepcopy(base))
        try:
            definition=design(constraints=[rule()]); panel.apply_design(definition)
            self.assertEqual(panel.design(), definition)
            panel.generate()
            self.assertEqual(len(panel.generated_records(base)),2)
            self.assertIn('2 accepted',panel.status.text()); self.assertTrue(panel.rejected_button.isEnabled())
            panel.rejected_button.click()
            report=panel.findChild(RejectedConfigurationsDialog)
            self.assertIn('configuration_000003',report.text.toPlainText())
            self.assertIn('limit',report.text.toPlainText())
            report.search.setText('no match'); self.assertEqual(report.text.toPlainText(),'')
            report.close()
            panel.constraints.table.item(0,4).setText('-1')
            self.assertEqual(panel._records,[]); self.assertFalse(panel.rejected_button.isEnabled())
            panel.generate(); self.assertEqual(len(panel._rejected),3)
            self.assertFalse(panel.preview_button.isEnabled())
            with self.assertRaisesRegex(ValueError,'All configurations'):panel.generated_records(base)
            panel.constraints.table.setCurrentCell(0,0); panel.constraints.remove_button.click()
            self.assertNotIn('constraints',panel.design()); panel.generate()
            self.assertEqual(len(panel._records),3)
        finally: panel.close(); panel.deleteLater(); self.app.processEvents()

    def test_radian_rules_convert_and_project_preserves_definitions(self):
        from PySide6.QtCore import QSettings
        from jorvik.app import MainWindow
        from jorvik.geometry import load_geometry
        from jorvik.sampling import sample_geometry
        from jorvik.project import save_project,load_project
        from test_project import FIXTURE,state
        from jorvik.parametrisation_panel import ParametrisationDialog
        with TemporaryDirectory() as directory:
            window=MainWindow(QSettings(str(Path(directory)/'settings.ini'),QSettings.Format.IniFormat))
            geometry=load_geometry(FIXTURE); window.geometry=geometry
            request={key:geometry[key] for key in ('source','source_hash','document')}
            request.update(label='wall',target='interior',entities=[],method='sobol',count=8,seed=42,resolution=16)
            cloud=sample_geometry(request); window.sampling.batches=[cloud]
            definition=design(parameters=[parameter('angle','rotation_angle',start=0.,stop=math.pi,count=3,unit='radians')],
                              constraints=[rule('angle',value=math.pi/2)])
            setup=affine_setup(source_hash=geometry['source_hash'],angle_units='radians',design=definition)
            dialog=ParametrisationDialog(window)
            try:
                dialog.apply_setup(setup)
                actual=dialog.setup()['design']
                self.assertAlmostEqual(actual['constraints'][0]['right']['value'],90.)
                self.assertEqual(actual['parameters'][0]['unit'],'degrees')
                dialog.design_panel.generate(); self.assertEqual(len(dialog.design_panel._records),2)
                saved=state(); saved['parametrisation']=setup
                path=Path(directory)/'rules.jorvik'; save_project(path,geometry,[(cloud,'Generated',True)],saved)
                manifest,_=load_project(path)
                self.assertEqual(manifest['state']['parametrisation'],setup)
            finally:
                dialog.close(); window.confirm_unsaved_changes=lambda:True; window.close()

    def test_rejection_pagination_and_literal_names(self):
        from jorvik.constraint_panel import RejectedConfigurationsDialog
        name='wall / λ + __import__'
        definition=design(parameters=[parameter(name,mode='values',values=[0.1,0.1+1e-15])],
                          constraints=[rule(name,operator='==',value=0.1)])
        result=generate_design(affine_setup(),definition)
        self.assertEqual(len(result['accepted']),1); self.assertEqual(len(result['rejected']),1)
        rows=[{'id':f'configuration_{index:06d}','parameters':{name:float(index)},'reasons':['Literal reason']} for index in range(1,451)]
        dialog=RejectedConfigurationsDialog(rows,None)
        try:
            self.assertEqual(dialog.page.maximum(),3)
            dialog.page.setValue(3)
            self.assertIn('configuration_000401',dialog.text.toPlainText())
            self.assertNotIn('configuration_000400',dialog.text.toPlainText())
            dialog.search.setText('000450'); self.assertIn('configuration_000450',dialog.text.toPlainText())
            self.assertEqual(dialog.page.maximum(),1)
        finally:dialog.close();dialog.deleteLater();self.app.processEvents()
