"""Parameter design order, reproducibility, bounds and preallocation limits."""
from copy import deepcopy
import unittest
from unittest.mock import patch

import numpy as np

from jorvik.parameter_design import (MAX_CONFIGURATIONS, design_summary,
                                      generate_configurations, validate_design)
from test_affine_transforms import affine_setup
from test_export import batch


def parameter(name='shift', target='translation_x', **fields):
    value = {'name': name, 'target': target, 'mode': 'range', 'start': 0.,
             'stop': 1., 'count': 3, 'reference': 0., 'unit': 'source'}
    if target == 'rotation_angle': value['unit'] = 'degrees'
    if target.startswith('scale_') or target == 'uniform_scale':
        value.update(start=1., stop=2., reference=1., unit='dimensionless')
    value.update(fields)
    return value


def design(method='cartesian', parameters=None, **fields):
    value = {'schema_version': 1, 'method': method, 'seed': 17, 'count': 16,
             'parameters': parameters if parameters is not None else [parameter()]}
    value.update(fields)
    return value


class ParameterDesignTest(unittest.TestCase):
    def test_cartesian_order_actual_values_binding_and_nonmutation(self):
        definition = design(parameters=[parameter(count=2), parameter('angle', 'rotation_angle',
            mode='values', values=[0., 90., 180.])])
        base = affine_setup(translation=[5., 6., 7.]); base['design'] = deepcopy(definition)
        old_base, old_definition = deepcopy(base), deepcopy(definition)
        records = generate_configurations(base, definition)
        self.assertEqual([r['parameters'] for r in records], [
            {'shift': 0., 'angle': 0.}, {'shift': 0., 'angle': 90.},
            {'shift': 0., 'angle': 180.}, {'shift': 1., 'angle': 0.},
            {'shift': 1., 'angle': 90.}, {'shift': 1., 'angle': 180.}])
        self.assertEqual([r['id'] for r in records], [f'configuration_{i:06d}' for i in range(1,7)])
        for record in records:
            setup = record['setup']; values = record['parameters']
            self.assertEqual(setup['translation'], [values['shift'], 6., 7.])
            self.assertEqual(setup['rotation_angle'], values['angle'])
            self.assertNotIn('design', setup)
            self.assertEqual(setup['design_configuration']['parameters'], values)
            self.assertEqual(setup['design_configuration']['units'], {'shift':'source','angle':'degrees'})
            self.assertEqual(setup['design_configuration']['bindings'], {'shift':'translation_x','angle':'rotation_angle'})
        records[0]['setup']['translation'][1] = 100
        self.assertEqual(records[1]['setup']['translation'][1],6.)
        self.assertEqual(base, old_base); self.assertEqual(definition, old_definition)

    def test_paired_explicit_lists_constant_ranges_and_uniform_scale(self):
        definition = design('paired', [parameter(mode='values', values=[4., -2.]),
            parameter('size','uniform_scale',mode='values', values=[1., 3.])])
        records = generate_configurations(affine_setup(), definition)
        self.assertEqual([r['setup']['scale'] for r in records],[[1.,1.,1.],[3.,3.,3.]])
        self.assertEqual([r['setup']['translation'][0] for r in records],[4.,-2.])
        definition['parameters'][1]['values'].append(2.)
        with self.assertRaises(ValueError): validate_design(definition)
        for method in ('random','lhs','sobol'):
            definition = design(method,[parameter(start=2.,stop=2.)])
            self.assertEqual({r['parameters']['shift'] for r in generate_configurations(affine_setup(),definition)},{2.})

    def test_seeded_designs_reproducible_strata_and_global_rng_untouched(self):
        for method in ('random','lhs','sobol'):
            definition = design(method,[parameter(),parameter('y','translation_y')])
            before = np.random.get_state()
            first = generate_configurations(affine_setup(),definition)
            second = generate_configurations(affine_setup(),definition)
            self.assertEqual(first,second)
            after = np.random.get_state()
            self.assertEqual(before[0],after[0]);np.testing.assert_array_equal(before[1],after[1])
            self.assertEqual(before[2:],after[2:])
            values = np.asarray([[r['parameters']['shift'],r['parameters']['y']] for r in first])
            self.assertTrue(np.all((values >= 0) & (values < 1)))
            if method in ('lhs','sobol'):
                for column in values.T:
                    np.testing.assert_array_equal(np.sort(np.floor(column*16).astype(int)),np.arange(16))
            different = generate_configurations(affine_setup(),dict(definition,seed=18))
            self.assertNotEqual(first,different)
        definition = design('sobol',count=7)
        rows = generate_configurations(affine_setup(),definition)
        self.assertEqual(len(rows),7)
        self.assertTrue(any('power-of-two' in warning for warning in design_summary(definition)['warnings']))

    def test_summary_numeric_estimate_and_huge_cartesian_rejected_before_allocation(self):
        a,b = batch(count=3),batch('other',count=5)
        a['normals']=[[0.,0.,1.]]*3
        summary = design_summary(design(parameters=[parameter(count=2),parameter('y','translation_y',count=3)]),[a,b])
        self.assertEqual(summary['count'],6)
        self.assertEqual(summary['parameter_count'],2)
        self.assertEqual(summary['points_per_configuration'],8)
        self.assertEqual(summary['total_points'],48)
        self.assertEqual(summary['estimated_bytes'],6*(3*52+5*28))
        self.assertEqual(summary['per_region'],[{'label':'boundary','points':3},{'label':'other','points':5}])
        huge = design(parameters=[parameter(count=MAX_CONFIGURATIONS),parameter('y','translation_y',count=MAX_CONFIGURATIONS)])
        with patch('numpy.linspace') as allocate:
            with self.assertRaises(ValueError):generate_configurations(affine_setup(),huge)
            allocate.assert_not_called()
        self.assertEqual(design_summary(design(parameters=[parameter(count=MAX_CONFIGURATIONS)]))['count'],MAX_CONFIGURATIONS)

    def test_validation_rejects_ambiguous_invalid_and_incompatible_definitions(self):
        bad = [None,[],dict(design(),schema_version=2),dict(design(),method='mesh'),
               dict(design(),seed=True),dict(design(),seed=-1),dict(design(),parameters=[]),
               design('random',count=0),design('sobol',count=MAX_CONFIGURATIONS+1)]
        invalid_fields = [{'name':''},{'target':'radius'},{'unit':'mm'}, {'start':float('nan')},
                          {'stop':float('inf')},{'start':2.,'stop':1.},{'count':True},
                          {'mode':'values','values':[]},{'mode':'values','values':[False]},
                          {'reference':float('nan')},{'mode':'range','start':-1e308,'stop':1e308}]
        bad.extend(design(parameters=[parameter(**changes)]) for changes in invalid_fields)
        bad.extend([design(parameters=[parameter(),parameter()]),
                    design(parameters=[parameter(),parameter('shift','translation_y')]),
                    design(parameters=[parameter('all','uniform_scale'),parameter('x','scale_x')]),
                    design(parameters=[parameter('x','scale_x',start=0.)]),
                    design(parameters=[parameter('x','scale_x',reference=-1.)]),
                    design('lhs',[parameter(mode='values',values=[1.])])])
        for definition in bad:
            with self.subTest(definition=definition),self.assertRaises(ValueError):validate_design(definition)
        radians = design(parameters=[parameter('angle','rotation_angle',unit='radians')])
        validate_design(radians,affine_setup(angle_units='radians'))
        with self.assertRaises(ValueError): validate_design(radians,affine_setup())
        values_only = parameter(mode='values',values=[0.,1.]); del values_only['count']
        validate_design(design(parameters=[values_only]))
        stochastic = parameter(); del stochastic['count']
        validate_design(design('random',[stochastic]))

    def test_persisted_large_value_list_is_not_copied_per_configuration(self):
        class TrackedValues(list):
            copies = 0
            def __deepcopy__(self, memo):
                type(self).copies += 1
                return list(self)
        values = TrackedValues(range(100))
        definition = design(parameters=[parameter(mode='values',values=values)])
        base = affine_setup(); base['design'] = definition
        records = generate_configurations(base,definition)
        self.assertEqual(len(records),100)
        self.assertLessEqual(TrackedValues.copies,1)
        self.assertTrue(all('design' not in row['setup'] for row in records))

    def test_invalid_generated_transform_fails_with_stable_id(self):
        base = affine_setup(pivot=[1e308,0.,0.])
        definition = design(parameters=[parameter('size','scale_x',mode='values',values=[1.,10.])])
        old = deepcopy(base)
        with self.assertRaisesRegex(ValueError,'configuration_000002'):
            generate_configurations(base,definition)
        self.assertEqual(base,old)
        with self.assertRaises(ValueError):generate_configurations(dict(base,schema_version=1),definition)
