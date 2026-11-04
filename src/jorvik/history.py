"""Session edit history; share immutable numeric clouds across metadata snapshots."""
from copy import deepcopy
from functools import wraps
from inspect import signature
from PySide6.QtCore import Qt


def copy_records(value):
    memo = {}
    def visit(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if key in ('coordinates', 'entity_tags', 'normals'):
                    memo[id(child)] = child
                else:
                    visit(child)
        elif isinstance(item, (list, tuple)):
            for child in item: visit(child)
    visit(value)
    return deepcopy(value, memo)


def undoable(title):
    def decorate(function):
        argument_count = len(signature(function).parameters) - 1
        @wraps(function)
        def wrapped(self, *args, **kwargs):
            window = self if isinstance(self, HistoryActions) else self.window
            before = window.edit_snapshot()
            # Qt signals may supply a checked/result argument to a no-argument slot.
            result = function(self, *args[:argument_count], **kwargs)
            window.record_edit(title, before)
            return result
        return wrapped
    return decorate


class HistoryActions:
    def initialize_history(self):
        self._history_entries = []
        self._history_index = 0
        self._history_suspended = False
        menu = self.menuBar().addMenu('&Edit')
        self.undo_action = menu.addAction('Undo', self.undo_edit, 'Ctrl+Z')
        self.redo_action = menu.addAction('Redo', self.redo_edit, 'Ctrl+Shift+Z')
        self.redo_action.setShortcuts(['Ctrl+Shift+Z', 'Ctrl+Y'])
        self.refresh_history_actions()

    def reset_history(self):
        self._history_entries.clear()
        self._history_index = 0
        self._history_suspended = False
        self.refresh_history_actions()

    def edit_snapshot(self):
        if not hasattr(self, '_history_entries') or self._history_suspended or not self.geometry:
            return None
        return copy_records({
            'areas': [(area, status, self.sampled_areas.items[label].checkState(0) == Qt.CheckState.Checked)
                      for label, (area, status) in self.sampled_areas.areas.items()],
            'regions': self.boundary_regions, 'setup': self.parametrisation_setup,
            'selected': self.selected_area, 'isolate': self.isolate_action.isChecked(),
            'context': self.isolate_geometry_action.isChecked(), 'label': self.sampling.label.text()})

    def record_edit(self, title, before):
        if before is None: return
        after = self.edit_snapshot()
        if before == after: return
        del self._history_entries[self._history_index:]
        self._history_entries.append((title, before, after))
        self._history_entries = self._history_entries[-30:]
        self._history_index = len(self._history_entries)
        self.refresh_history_actions()

    def refresh_history_actions(self):
        if not hasattr(self, 'undo_action'): return
        busy = bool(self.project_busy())
        undo = self._history_index > 0
        redo = self._history_index < len(self._history_entries)
        self.undo_action.setEnabled(undo and not busy)
        self.redo_action.setEnabled(redo and not busy)
        self.undo_action.setText('Undo ' + self._history_entries[self._history_index-1][0] if undo else 'Undo')
        self.redo_action.setText('Redo ' + self._history_entries[self._history_index][0] if redo else 'Redo')

    def restore_edit(self, snapshot):
        state = copy_records(snapshot)
        self.clear_isolation()
        self.sampled_areas.clear_areas()
        self.sampling.batches = []
        for area, status, visible in state['areas']:
            self.sampled_areas.set_area(area, status)
            if status == 'Generated':
                self.sampling.batches.append(area)
                self.sampled_areas.items[area['label']].setCheckState(0, Qt.CheckState.Checked if visible else Qt.CheckState.Unchecked)
        self.boundary_regions = state['regions']
        self.parametrisation_setup = state['setup']
        self.refresh_boundary_regions()
        self.sampling.label.setText(state['label'])
        if state['selected'] in self.sampled_areas.areas:
            self.show_sampled_area(state['selected'])
            self.isolate_geometry_action.setChecked(state['context'])
            self.isolate_action.setChecked(state['isolate'])
        self.sampling.refresh_preview()
        if self.selected_area is None: self.update_selection()
        self.sampling.summary.setText(f'{len(self.sampling.batches)} generated datasets.')

    def undo_edit(self):
        if self.project_busy() or self._history_index == 0: return
        self._history_index -= 1
        title, before, _ = self._history_entries[self._history_index]
        self.restore_edit(before)
        self.refresh_history_actions()
        self.statusBar().showMessage('Undid ' + title)

    def redo_edit(self):
        if self.project_busy() or self._history_index == len(self._history_entries): return
        title, _, after = self._history_entries[self._history_index]
        self.restore_edit(after)
        self._history_index += 1
        self.refresh_history_actions()
        self.statusBar().showMessage('Redid ' + title)

    @undoable('duplicate dataset')
    def duplicate_area(self, label):
        if self.project_busy() or label not in self.sampled_areas.areas: return
        area, status = self.sampled_areas.areas[label]
        if status != 'Generated': return
        record = copy_records(area)
        record['label'] = self.sampling.next_available_label(label)
        self.sampling.batches.append(record)
        self.sampled_areas.set_area(record)
        self.show_sampled_area(record['label'])
        self.sampling.refresh_preview()

    @undoable('delete dataset')
    def delete_area(self, label):
        if self.project_busy() or label not in self.sampled_areas.areas: return
        # Rebuild the tree through the same path as undo so selection, isolation
        # and parametrisation references cannot retain a deleted label.
        state = self.edit_snapshot()
        state['areas'] = [row for row in state['areas'] if row[0]['label'] != label]
        if state['setup']:
            state['setup']['regions'] = [name for name in state['setup']['regions'] if name != label]
            if state['setup']['scope'] == 'regions' and not state['setup']['regions']:
                state['setup'] = None
        if state['selected'] == label:
            state.update(selected=None, isolate=False, context=False)
        self.restore_edit(state)
