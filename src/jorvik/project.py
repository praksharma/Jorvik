"""Versioned portable project archives, numeric arrays without pickle or CAD execution."""
import hashlib
import json
import os
import tempfile
from pathlib import Path
import zipfile
import numpy as np
from .cad_import import FORMATS, validate_import_metadata
from .sampling_methods import METHODS
from .topology import validate_topology
from .regions import validate_regions
from .export import atomic_write, to_xarray

PROJECT_EXTENSION = ".jorvik"
PROJECT_EXTENSIONS = (PROJECT_EXTENSION, ".gsproj")
PROJECT_FILTER = "Jorvik project (*.jorvik *.gsproj)"


def save_project(path, geometry, areas, state):
    if geometry is None:
        if areas or state != {}:
            raise ValueError('Empty projects cannot contain datasets or geometry state.')
        manifest = {'schema_version': 3, 'geometry': None, 'areas': [],
                    'state': {}, 'source_extension': None}
        def write_empty(temporary):
            with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('manifest.json', json.dumps(manifest, allow_nan=False))
        atomic_write(path, write_empty)
        return
    validate_topology(geometry)
    validate_import_metadata(geometry)
    validate_regions(state.get('boundary_regions', []), geometry)
    if state.get('parametrisation') is not None:
        from .parametrisation import validate_setup
        validate_setup(state['parametrisation'])
        if state['parametrisation']['source_hash'] != geometry['source_hash']:
            raise ValueError('Transform setup belongs to a different geometry.')
    source = Path(geometry['source'])
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != geometry['source_hash']:
        raise ValueError('Source geometry changed. Reopen it before saving.')
    metadata = {k: v for k, v in geometry.items() if k != 'entities'}
    metadata['entities'] = [{k:v for k,v in e.items() if k != 'positions'} for e in geometry['entities']]
    records=[]
    for area, status, visible in areas:
        validate_import_metadata(area)
        for field in ('source_format', 'import_options'):
            if field in area and field in geometry and area[field] != geometry[field]:
                raise ValueError('Dataset import metadata differs from project geometry.')
        if status == 'Generating':
            raise ValueError('Wait for generation to finish before saving.')
        records.append({'area': {k:v for k,v in area.items() if k not in ('coordinates','entity_tags','normals')},
                        'status':status,'visible':visible,'has_normals':status == 'Generated' and 'normals' in area})
    generated = [area for area, status, _ in areas if status == 'Generated']
    if generated:
        dataset=to_xarray(generated);dataset.close()
    manifest={'schema_version':2,'geometry':metadata,'areas':records,'state':state,
              'source_extension':source.suffix.lower()}
    def write(temporary):
        with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=3) as archive:
            archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,allow_nan=False))
            archive.writestr('source/model'+source.suffix.lower(),data)
            def array(name,value):
                with archive.open(name,'w',force_zip64=True) as stream:
                    np.save(stream,np.asarray(value),allow_pickle=False)
            for i,entity in enumerate(geometry['entities']):
                array(f'geometry/{i}.npy',np.asarray(entity['positions'],dtype=np.float64))
            for i,(area,status,_) in enumerate(areas):
                if status=='Generated':
                    array(f'areas/{i}_coordinates.npy',np.asarray(area['coordinates'],dtype=np.float64))
                    array(f'areas/{i}_tags.npy',np.asarray(area['entity_tags'],dtype=np.int32))
                    if 'normals' in area:
                        array(f'areas/{i}_normals.npy',np.asarray(area['normals'],dtype=np.float64))
    atomic_write(path,write)


def create_empty_project(path, *, overwrite=False):
    """Create a geometry-free .jorvik document without Qt; refuse replacement by default.

    Return the path. Both creation and explicit replacement are atomic. Existing
    project readers continue to accept populated schema 1/2 documents unchanged.
    """
    path = Path(path)
    if path.suffix.lower() not in PROJECT_EXTENSIONS:
        raise ValueError('Choose a Jorvik project (.jorvik or legacy .gsproj) file.')
    if overwrite:
        save_project(path, None, [], {})
    else:
        # Publish a complete archive with an exclusive hard link. Unlike a prior
        # exists() check plus replace(), this cannot clobber a concurrent creator.
        with tempfile.TemporaryDirectory(prefix='.jorvik-create-', dir=path.parent) as directory:
            temporary = Path(directory) / ('empty' + PROJECT_EXTENSION)
            save_project(temporary, None, [], {})
            os.link(temporary, path)
    return path


def load_project(path):
    """Fully validate before returning a candidate; never mutate the active project."""
    with zipfile.ZipFile(path) as archive:
        manifest=json.loads(archive.read('manifest.json'))
        if not isinstance(manifest, dict) or manifest.get('schema_version') not in (1, 2, 3):
            raise ValueError('Unsupported Jorvik project version.')
        if manifest.get('schema_version') == 3 and manifest.get('geometry', 'missing') is None:
            if (manifest.get('areas') != [] or manifest.get('state') != {}
                    or 'source_extension' not in manifest or manifest['source_extension'] is not None
                    or archive.namelist() != ['manifest.json']):
                raise ValueError('Invalid empty project document.')
            return manifest, None
        extension=manifest['source_extension']
        if extension not in FORMATS:
            raise ValueError('Unsupported geometry format in project.')
        source=archive.read('source/model'+extension)
        geometry=manifest['geometry']
        validate_import_metadata(geometry)
        if geometry.get('source_format', FORMATS[extension]) != FORMATS[extension]:
            raise ValueError('Project source format differs from its embedded geometry.')
        if hashlib.sha256(source).hexdigest()!=geometry['source_hash']:
            raise ValueError('Project geometry checksum does not match.')
        def array(name):
            with archive.open(name) as stream:
                return np.load(stream,allow_pickle=False)
        keys=set()
        for i,entity in enumerate(geometry['entities']):
            key=(entity['dimension'],entity['tag'])
            if key in keys or key[0] not in (1,2) or key[1]<=0:
                raise ValueError('Invalid geometry entity in project.')
            keys.add(key)
            points=array(f'geometry/{i}.npy')
            if points.ndim!=1 or len(points)%3 or not np.isfinite(points).all():
                raise ValueError('Invalid display coordinates in project.')
            entity['positions']=points.tolist()
        labels=set();batches=[]
        for i,record in enumerate(manifest['areas']):
            area=record['area'];label=area['label'];status=record['status']
            validate_import_metadata(area)
            for field in ('source_format', 'import_options'):
                if field in area and field in geometry and area[field] != geometry[field]:
                    raise ValueError('Dataset import metadata differs from project geometry.')
            if not isinstance(label,str) or not label.strip() or label in labels:
                raise ValueError('Invalid or duplicate region label in project.')
            labels.add(label)
            if status=='Generating' or not (status=='Generated' or status.startswith('Not generated')):
                raise ValueError('Invalid sampling status in project.')
            if area['method'] not in METHODS:
                raise ValueError('Unknown sampling method in project.')
            if area['document']!=geometry['document'] or area['source_hash']!=geometry['source_hash']:
                raise ValueError('Region belongs to another geometry.')
            if status=='Generated':
                area['coordinates']=array(f'areas/{i}_coordinates.npy').tolist()
                area['entity_tags']=array(f'areas/{i}_tags.npy').tolist()
                if type(record.get('has_normals', False)) is not bool:
                    raise ValueError('Invalid saved normal availability.')
                if record.get('has_normals'):
                    area['normals']=array(f'areas/{i}_normals.npy').tolist()
                if area['entity_dimension'] != 3 and not {tuple(e) for e in area['entities']}<=keys:
                    raise ValueError('Region references missing geometry entities.')
                batches.append(area)
        if batches:
            dataset=to_xarray(batches);dataset.close()
        # UI state is checked independently and rejected before replacing the workspace.
        state=manifest['state']
        if not {tuple(e) for e in state.get('selection',[])}<=keys:
            raise ValueError('Saved selection references missing entities.')
        validate_state(geometry,manifest['areas'],state)
        return manifest,source


def validate_state(geometry, areas, state):
    """Reject corrupt UI recipes before any widgets or current data are replaced."""
    validate_topology(geometry)
    regions = state.get('boundary_regions', [])
    validate_regions(regions, geometry)
    for record in areas:
        area = record['area']
        if 'boundary_region' in area:
            snapshot = area['boundary_region']
            validate_regions([snapshot], geometry)
            if sorted(snapshot['entities']) != sorted(area['entities']):
                raise ValueError('Saved boundary region differs from sampling target.')
    if not isinstance(geometry['source'],str) or not isinstance(geometry['name'],str) or geometry['dimension'] not in (2,3):
        raise ValueError('Invalid geometry metadata.')
    for entity in geometry['entities']:
        if not isinstance(entity['type'],str) or not isinstance(entity['boundary'],list) or not isinstance(entity['groups'],list):
            raise ValueError('Invalid geometry topology metadata.')
        expected=9 if entity['dimension']==2 else 6
        if len(entity['positions'])%expected:
            raise ValueError('Invalid display primitives.')
    for group in geometry['groups']:
        if group['dimension'] not in (0,1,2,3) or not isinstance(group['entities'],list) or not isinstance(group['name'],str):
            raise ValueError('Invalid group metadata.')
    if 'recovery_origin' in state and state['recovery_origin'] is not None and not isinstance(state['recovery_origin'], str):
        raise ValueError('Invalid recovery project reference.')
    labels={r['area']['label'] for r in areas}
    if state.get('selected_area') is not None and state['selected_area'] not in labels:
        raise ValueError('Missing selected area.')
    if state.get('isolate') and not any(r['area']['label']==state.get('selected_area') and r['status']=='Generated' for r in areas):
        raise ValueError('Invalid isolated region.')
    if state.get('mode') not in ('face','edge'):
        raise ValueError('Invalid selection mode.')
    for key in ('show_points','through','show_grid','show_axes','isolate','isolate_geometry'):
        if type(state[key]) is not bool:raise ValueError('Invalid visibility settings.')
    if type(state['point_size']) is not int or not 3<=state['point_size']<=16:
        raise ValueError('Invalid point size.')
    visibility=state['geometry_visibility']
    for key in ('geometry','faces','edges'):
        if type(visibility[key]) is not bool:raise ValueError('Invalid geometry visibility.')
    face_tags={e['tag'] for e in geometry['entities'] if e['dimension']==2}
    if not set(visibility['hiddenFaces'])<=face_tags or (visibility['isolatedFaces'] is not None and not set(visibility['isolatedFaces'])<=face_tags):
        raise ValueError('Missing visible/hidden face.')
    isolated=visibility.get('isolatedEntities')
    keys={(e['dimension'],e['tag']) for e in geometry['entities']}
    if isolated is not None and not {tuple(e) for e in isolated}<=keys:raise ValueError('Invalid isolated geometry.')
    controls=state['controls']
    if controls['method'] not in METHODS or controls['compute_mode'] not in ('multicore','reproducible') or controls['resolution'] not in (32,64):
        raise ValueError('Invalid saved sampling settings.')
    for field,low,high in [('count',1,50000),('seed',0,2147483647),('cpu_threads',1,64)]:
        if type(controls[field]) is not int or not low<=controls[field]<=high:raise ValueError('Invalid sampling '+field)
    if not isinstance(controls['label'],str):raise ValueError('Invalid next dataset label.')
    if controls['target'] not in ('selection','interior') and not isinstance(controls['target'],dict):raise ValueError('Invalid target.')
    if isinstance(controls['target'], dict) and 'region_id' in controls['target']:
        if controls['target']['region_id'] not in {r['id'] for r in regions}:
            raise ValueError('Saved sampling target references a missing boundary region.')
    camera=state.get('camera')
    if camera:
        for key in ('position','target','up'):
            values=np.asarray(camera[key],dtype=float)
            if values.shape!=(3,) or not np.isfinite(values).all():raise ValueError('Invalid camera state.')
        if not np.isfinite([camera['near'],camera['far']]).all() or not 0<camera['near']<camera['far']:
            raise ValueError('Invalid camera clipping range.')
    if state.get('layout'):
        bytes.fromhex(state['layout'])
    setup=state.get('parametrisation')
    if setup is not None:
        from .parametrisation import validate_setup
        validate_setup(setup)
        if setup['source_hash'] != geometry['source_hash'] or not set(setup['regions']) <= labels:
            raise ValueError('Invalid parametrisation references or values.')
