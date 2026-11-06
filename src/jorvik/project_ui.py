"""Project menu actions and workspace restoration."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
from PySide6.QtCore import Qt, QByteArray, QEventLoop, QTimer
from PySide6.QtWidgets import QFileDialog, QMessageBox, QProgressBar
from .project import save_project, load_project, PROJECT_EXTENSION, PROJECT_FILTER
from .recovery import RecoveryActions


class ProjectActions(RecoveryActions):
    def project_busy(self):
        return self.sampling.process or self.import_process or self.parametrisation_dialog or self.boundary_dialog or self.saving_project or self.project_io

    def refresh_project_actions(self):
        if hasattr(self, 'sampling'):
            available = self.sampling.recipe_available()
            self.sampling.save_recipe_button.setEnabled(available)
            self.sampling.load_recipe_button.setEnabled(available)
        busy=bool(self.project_busy())
        self.open_project_action.setEnabled(not busy)
        self.save_project_action.setEnabled(not busy)
        self.save_project_as_action.setEnabled(not busy)
        self.refresh_history_actions()

    def project_state(self, camera=None):
        panel=self.sampling
        return {'selection':[list(key) for key in sorted(self.selection)],
                'mode':self.view.mode,'geometry_visibility':deepcopy(self.view.geometry_visibility),
                'selected_area':self.selected_area,'isolate':self.isolate_action.isChecked(),
                'isolate_geometry':self.isolate_geometry_action.isChecked(),
                'show_points':panel.show_points.isChecked(),'through':panel.through.isChecked(),
                'point_size':panel.point_size.value(),'show_grid':self.view.show_grid,'show_axes':self.view.show_axes,
                'controls':{'method':panel.method.currentData(),'target':panel.target.currentData(),
                            'count':panel.count.value(),'seed':panel.seed.value(),'label':panel.label.text(),
                            'resolution':panel.resolution.currentData(),'compute_mode':panel.compute_mode.currentData(),
                            'cpu_threads':panel.cpu_threads.value()},
                'boundary_regions':deepcopy(self.boundary_regions),
                'parametrisation':deepcopy(self.parametrisation_setup),
                'layout':bytes(self.saveState()).hex(),'camera':camera}

    def project_io_call(self, operation):
        """Await one worker while Qt keeps rendering and processing events.

        Dataset editing is locked for the job, so the worker can borrow original
        numeric lists without duplicating an entire project on the UI thread.
        """
        from .project_worker import ProjectWorker
        if self.project_io is not None:
            raise ValueError('Another project operation is already running.')
        worker = ProjectWorker(operation, self)
        loop = QEventLoop()
        locked = (self.sampling, self.tree, self.sampled_areas, self.boundary_button,
                  self.parametrisation_button, self.open_action, self.export_action)
        enabled = [control.isEnabled() for control in locked]
        self.project_io = worker
        for control in locked: control.setEnabled(False)
        self.refresh_project_actions()
        progress = QProgressBar(self)
        progress.setRange(0,0); progress.setAccessibleName('Project file operation')
        progress.setMaximumWidth(180)
        self.statusBar().addPermanentWidget(progress)
        worker.finished.connect(loop.quit)
        try:
            worker.start()
            loop.exec()
            worker.wait()
            if worker.error is not None: raise worker.error
            return worker.result
        finally:
            worker.wait()
            self.project_io = None
            self.statusBar().removeWidget(progress); progress.deleteLater()
            for control, previous in zip(locked, enabled): control.setEnabled(previous)
            self.refresh_project_actions()
            worker.deleteLater()
            QTimer.singleShot(0, self.retry_pending_close)

    def workspace_digest(self):
        """Compare content/recipes, excluding ordinary viewport navigation."""
        if not self.geometry: return None
        state = self.project_state()
        state = {key: state[key] for key in ('controls', 'boundary_regions', 'parametrisation')}
        areas = [(area, status, self.sampled_areas.items[label].checkState(0) == Qt.CheckState.Checked)
                 for label, (area, status) in self.sampled_areas.areas.items()]
        payload = {'document': self.geometry['document'], 'source_hash': self.geometry['source_hash'],
                   'state': state, 'areas': areas}
        def fingerprint():
            digest = hashlib.sha256()
            for chunk in json.JSONEncoder(sort_keys=True, allow_nan=False).iterencode(payload):
                digest.update(chunk.encode('utf-8'))
            return digest.hexdigest()
        return self.project_io_call(fingerprint)

    def mark_project_clean(self):
        self.saved_digest = self.workspace_digest()

    def has_unsaved_changes(self):
        return self.geometry is not None and self.workspace_digest() != self.saved_digest

    def confirm_unsaved_changes(self):
        if not self.has_unsaved_changes(): return True
        answer = QMessageBox.question(self, 'Unsaved project changes',
            'Save your changes before replacing or closing this workspace?',
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Cancel: return False
        if answer == QMessageBox.StandardButton.Save:
            if self.project_busy():
                QMessageBox.information(self, 'Project busy', 'Finish or cancel processing before saving the project.')
                return False
            return self.save_project()
        return answer == QMessageBox.StandardButton.Discard

    def save_project(self, save_as=False):
        if self.project_busy():
            return False
        path=self.project_path if not save_as else None
        if not path:
            suggested = (self.project_path.with_suffix(PROJECT_EXTENSION) if self.project_path else
                         Path((Path(self.geometry['name']).stem if self.geometry else 'Untitled') + PROJECT_EXTENSION))
            filename,_=QFileDialog.getSaveFileName(self,'Save Jorvik project',str(suggested),PROJECT_FILTER)
            if not filename:return False
            path=Path(filename)
            if not path.suffix:path=path.with_suffix(PROJECT_EXTENSION)
        result = []
        loop = QEventLoop()
        self.saving_project = True
        self.refresh_project_actions()
        def write(camera_json):
            try:
                camera=json.loads(camera_json) if camera_json else None
                areas=[(area,status,self.sampled_areas.items[label].checkState(0)==Qt.CheckState.Checked)
                       for label,(area,status) in self.sampled_areas.areas.items()]
                state = self.project_state(camera) if self.geometry is not None else {}
                self.project_io_call(lambda: save_project(path,self.geometry,areas,state))
                self.project_path=Path(path)
                self.setWindowTitle('Jorvik — '+self.project_path.name)
                self.statusBar().showMessage('Project saved: '+str(path))
                self.mark_project_clean()
                self.clear_recovery()
                result.append(True)
            except (ValueError,TypeError,OSError,KeyError) as error:
                QMessageBox.warning(self,'Could not save project',str(error))
                result.append(False)
            finally:
                self.saving_project = False
                self.refresh_project_actions()
                loop.quit()
                QTimer.singleShot(0, self.retry_pending_close)
        if self.view.is_ready:
            self.view.web.page().runJavaScript('JSON.stringify(window.geoViewer.cameraState())',write)
        else:write(None)
        if not result: loop.exec()
        return bool(result and result[0])

    def open_project(self):
        if self.project_busy():return
        filename,_=QFileDialog.getOpenFileName(self,'Open Jorvik project','',PROJECT_FILTER)
        if filename:self.load_project_file(filename)

    def load_project_file(self, filename, *, raise_errors=False):
        if self.project_busy():return
        cache=None
        try:
            manifest,source=self.project_io_call(lambda: load_project(filename))
            dirty = self.has_unsaved_changes()
            if not self.confirm_unsaved_changes(): return
            # Saving may have replaced this same file while the prompt was open.
            if dirty: manifest,source=self.project_io_call(lambda: load_project(filename))
            geometry=manifest['geometry']
            if geometry is None:
                old_cache = self.project_cache
                self.show_geometry(None)
                self.project_cache = None
                if old_cache is not None: old_cache.cleanup()
                self.project_path = Path(filename).resolve()
                self.setWindowTitle('Jorvik — ' + self.project_path.name)
                self.statusBar().showMessage('Empty project opened — import geometry to begin.')
                self.mark_project_clean()
                self.refresh_project_actions()
                return True
            # Retain a valid original path for relative .geo dependencies; otherwise
            # restore the embedded main CAD source without extracting arbitrary paths.
            path=Path(geometry['source'])
            try:
                valid=self.project_io_call(lambda: path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==geometry['source_hash'])
            except OSError:
                valid=False
            if self.project_cache and path.is_relative_to(self.project_cache.name):valid=False
            if not valid:
                cache=tempfile.TemporaryDirectory(prefix='jorvik-project-')
                path=Path(cache.name)/('model'+manifest['source_extension'])
                self.project_io_call(lambda: path.write_bytes(source))
            geometry['source']=str(path)
            for record in manifest['areas']:
                record['area']['source']=str(path)
            state=manifest['state']
            old_cache=self.project_cache
            self.show_geometry(geometry)
            self.boundary_regions = state.get('boundary_regions', [])
            self.refresh_boundary_regions()
            self.project_cache=cache
            self.project_path=Path(filename)
            if old_cache is not None:old_cache.cleanup()
            for record in manifest['areas']:
                area,status=record['area'],record['status']
                if status=='Generated':self.sampling.batches.append(area)
                self.sampled_areas.set_area(area,status)
                if status=='Generated' and not record['visible']:
                    self.sampled_areas.items[area['label']].setCheckState(0,Qt.CheckState.Unchecked)
            self.view.set_mode(state.get('mode','face'))
            self.selection_actions[self.view.mode].setChecked(True)
            self.selection={tuple(e) for e in state.get('selection',[])};self.update_selection()
            for key in ('geometry','faces','edges'):
                self.visibility_actions[key].setChecked(state['geometry_visibility'][key])
            self.view.set_geometry_visibility(state['geometry_visibility'])
            self.sampling.show_points.setChecked(state['show_points']);self.sampling.through.setChecked(state['through'])
            self.sampling.point_size.setValue(state['point_size'])
            self.set_visibility('show_grid',state['show_grid']);self.set_visibility('show_axes',state['show_axes'])
            for action,_ in self.icon_actions:
                if action.text()=='Grid':action.setChecked(state['show_grid'])
                if action.text()=='Axes':action.setChecked(state['show_axes'])
            controls=state['controls'];panel=self.sampling
            for field in ('method','target','resolution','compute_mode'):
                combo=getattr(panel,field)
                index=next((i for i in range(combo.count()) if combo.itemData(i)==controls[field]),0)
                combo.setCurrentIndex(index)
            for field in ('count','seed','cpu_threads'):getattr(panel,field).setValue(controls[field])
            panel.label.setText(controls['label'])
            self.parametrisation_setup=state.get('parametrisation')
            label=state.get('selected_area')
            if label in self.sampled_areas.items:
                self.sampled_areas.setCurrentItem(self.sampled_areas.items[label])
                self.isolate_geometry_action.setChecked(state['isolate_geometry'])
                self.isolate_action.setChecked(state['isolate'])
            self.sampling.refresh_preview()
            self.sampling.summary.setText(f'Restored {len(self.sampling.batches)} generated areas.')
            if state.get('layout'):self.restoreState(QByteArray.fromHex(state['layout'].encode()))
            if state.get('camera'):self.view.set_camera_state(state['camera'])
            self.setWindowTitle('Jorvik — '+self.project_path.name)
            self.statusBar().showMessage('Project opened: '+str(filename))
            if 'recovery_origin' in state:
                self.project_path = None  # Recovery always requires an explicit Save As destination.
                self.recovered_from = Path(filename)
                self.saved_digest = None
                self.setWindowTitle('Jorvik — Recovered unsaved project')
            else:
                self.mark_project_clean()
            if not valid and manifest['source_extension']=='.geo':
                self.statusBar().showMessage('Project restored. Embedded .geo source is available; external Include/Merge dependencies may need restoring before regeneration.')
            return True
        except Exception as error:
            if cache is not None and cache is not self.project_cache:cache.cleanup()
            if raise_errors: raise
            QMessageBox.warning(self,'Could not open project',str(error))
            return False

    def remap_parameter_label(self, old, new):
        if self.parametrisation_setup:
            self.parametrisation_setup['regions']=[new if label==old else label for label in self.parametrisation_setup.get('regions',[])]
