"""GUI save/load actions for validated sampling recipes."""
from pathlib import Path
from PySide6.QtWidgets import QFileDialog, QMessageBox
from copy import deepcopy

RECIPE_FILTER = 'Jorvik sampling recipe (*.json)'


class SamplingRecipeActions:
    def recipe_available(self):
        return (self.isEnabled() and not self._recipe_busy and self.window.geometry is not None and self.process is None
                and self.window.import_process is None and not self.window.saving_project
                and self.window.project_io is None)

    def save_sampling_recipe(self):
        if not self.recipe_available(): return
        try:
            from .sampling_recipe import create_recipe, save_recipe
            recipe = create_recipe(self.build_request())
            filename, _ = QFileDialog.getSaveFileName(self, 'Save sampling recipe', 'sampling-recipe.json', RECIPE_FILTER)
            if not filename: return
            path = Path(filename)
            save_recipe(recipe, path if path.suffix else path.with_suffix('.json'))
            self.summary.setText('Sampling recipe saved. No samples were generated.')
        except (ValueError, TypeError, KeyError, OSError) as error:
            QMessageBox.warning(self, 'Sampling recipe', str(error))

    def load_sampling_recipe(self):
        if not self.recipe_available(): return
        filename, _ = QFileDialog.getOpenFileName(self, 'Load sampling recipe', '', RECIPE_FILTER)
        if not filename: return
        try:
            from .sampling_recipe import load_recipe
            self.apply_sampling_recipe(load_recipe(filename))
        except (ValueError, TypeError, KeyError, OSError) as error:
            QMessageBox.warning(self, 'Sampling recipe', str(error))

    def apply_sampling_recipe(self, recipe):
        if not self.recipe_available(): raise ValueError('Wait for the current operation before loading a recipe.')
        from .sampling_recipe import bind_recipe
        request = bind_recipe(recipe, self.window.geometry, self.window.boundary_regions)
        # Check every GUI limit and resolve targets before mutating any controls.
        for name, spin in (('count', self.count), ('seed', self.seed), ('cpu_threads', self.cpu_threads)):
            if not spin.minimum() <= request[name] <= spin.maximum():
                title = {'count': 'point count', 'seed': 'seed', 'cpu_threads': 'CPU core count'}[name]
                raise ValueError(f'Recipe {title} is outside this workspace/machine range; no settings were changed.')
        accuracy = self.resolution.findData(request['resolution'])
        if accuracy < 0: raise ValueError('Recipe accuracy is not supported by this workspace; no settings were changed.')
        target = request['target']
        if target == 'region':
            data = {'region_id': request['boundary_region']['id']}
        elif target == 'interior' and len(request['entities']) == 1 and request['entities'][0][0] == 3 and self.target.findData({'volume_tag': request['entities'][0][1]}) >= 0:
            data = {'volume_tag': request['entities'][0][1]}
        elif target == 'group':
            data = {'dimension': request['entities'][0][0], 'tags': [item[1] for item in request['entities']]}
            # Group membership order is not significant.
            index = next((i for i in range(self.target.count()) if isinstance(self.target.itemData(i), dict)
                          and self.target.itemData(i).get('dimension') == data['dimension']
                          and set(self.target.itemData(i).get('tags', [])) == set(data['tags'])), -1)
            data = self.target.itemData(index) if index >= 0 else data
        else: data = target
        index = self.target.findData(data)
        if index < 0: raise ValueError('Recipe target is unavailable; no settings were changed.')
        ref = request.get('refinement')
        ref_index = 0
        if ref is not None:
            ref_dim = ref['entities'][0][0]
            ref_tags = {item[1] for item in ref['entities']}
            ref_index = -1
            for i in range(1, self.refinement_target.count()):
                item_data = self.refinement_target.itemData(i)
                if not isinstance(item_data, dict):
                    continue
                if 'region_id' in item_data:
                    region = next((r for r in self.window.boundary_regions if r['id'] == item_data['region_id']), None)
                    if region and region['entities'][0][0] == ref_dim and {e[1] for e in region['entities']} == ref_tags:
                        ref_index = i
                        break
                elif item_data.get('dimension') == ref_dim and set(item_data.get('tags', [])) == ref_tags:
                    ref_index = i
                    break
            if ref_index < 0:
                raise ValueError('Recipe refinement boundary target is unavailable; no settings were changed.')
        before = self.window.edit_snapshot()
        if before is not None: before['recipe_controls'] = self.recipe_control_state()
        self.target.setCurrentIndex(index)
        self.method.setCurrentIndex(self.method.findData(request['method']))
        self.count.setValue(request['count']); self.seed.setValue(request['seed'])
        self.label.setText(request['label']); self.resolution.setCurrentIndex(accuracy)
        self.compute_mode.setCurrentIndex(self.compute_mode.findData(request['compute_mode']))
        self.cpu_threads.setValue(request['cpu_threads'])
        self.refinement_target.setCurrentIndex(ref_index)
        if ref is not None:
            self.refinement_bias.setValue(ref['bias'])
            self.refinement_decay.setValue(ref['decay'])
            self.include_distance.setChecked(ref.get('include_distance', True))
        else:
            self.refinement_bias.setValue(0.0)
            self.refinement_decay.setValue(0.15)
            self.include_distance.setChecked(True)
        if target == 'selection':
            self.window.set_selection_mode('edge' if request['entities'][0][0] == 1 else 'face')
            self.window.selection = {tuple(item) for item in request['entities']}
            self.window.update_selection()
        after = self.window.edit_snapshot()
        if after is not None: after['recipe_controls'] = self.recipe_control_state()
        self.window.record_edit('load sampling recipe', before, after)
        self.summary.setText('Sampling recipe loaded. Review the target and settings, then click Generate samples. Existing datasets are unchanged.')

    def recipe_control_state(self):
        return {'combos': {name: deepcopy(getattr(self, name).currentData())
                           for name in ('target', 'method', 'resolution', 'compute_mode', 'refinement_target')},
                'spins': {name: getattr(self, name).value() for name in ('count', 'seed', 'cpu_threads', 'refinement_bias', 'refinement_decay')},
                'include_distance': self.include_distance.isChecked(),
                'label': self.label.text(), 'mode': self.window.view.mode,
                'selection': [list(item) for item in sorted(self.window.selection)]}

    def restore_recipe_controls(self, state):
        for name, value in state['combos'].items():
            if hasattr(self, name):
                combo = getattr(self, name); combo.setCurrentIndex(combo.findData(value))
        for name, value in state['spins'].items():
            if hasattr(self, name):
                getattr(self, name).setValue(value)
        if 'include_distance' in state:
            self.include_distance.setChecked(state['include_distance'])
        self.label.setText(state['label'])
        self.window.set_selection_mode(state['mode'])
        self.window.selection = {tuple(item) for item in state['selection']}
        self.window.update_selection()
