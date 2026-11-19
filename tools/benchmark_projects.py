"""Measure project I/O time and process peak RSS using an analytic annulus cloud.

This benchmarks persistence, not point generation or viewport rendering. Run each
count in a fresh process so peak RSS measurements are independent.
"""
import argparse
import json
from pathlib import Path
import resource
import sys
import tempfile
import time
import numpy as np
from jorvik.geometry import load_geometry
from jorvik.project import save_project, load_project


def run(count):
    if not 1 <= count <= 2000000: raise ValueError('Choose 1 to 2,000,000 points.')
    geometry=load_geometry(Path(__file__).resolve().parents[1]/'examples/geometries/cfdbench/annulus_unstructured.geo')
    angles=np.random.default_rng(42).uniform(0,2*np.pi,count)
    directions=np.column_stack((np.cos(angles),np.sin(angles),np.zeros(count)))
    points=.5*directions
    tags=np.array([2,1,4,3])[np.floor(angles/(np.pi/2)).astype(int)]
    batch={key:geometry[key] for key in ('document','source','source_hash','source_format')}
    batch.update(label='inner',target='selection',entities=[[1,i] for i in range(1,5)],
                 entity_dimension=1,method='random',seed=42,count=count,requested_count=count,
                 coordinates=points.tolist(),entity_tags=tags.tolist(),normals=(-directions).tolist(),
                 normal_status='available',normal_kind='outward_conormal',normal_validation='analytic annulus benchmark')
    state={'selection':[],'mode':'edge','selected_area':'inner','show_points':True,'through':False,
           'show_grid':False,'show_axes':True,'isolate':False,'isolate_geometry':False,'point_size':7,
           'geometry_visibility':{'geometry':True,'faces':True,'edges':True,'hiddenFaces':[],
                                  'isolatedFaces':None,'isolatedEntities':None},
           'controls':{'method':'random','compute_mode':'reproducible','resolution':32,'count':min(count,50000),
                       'seed':42,'cpu_threads':1,'label':'inner_1','target':'selection'},
           'layout':None,'camera':None,'parametrisation':None,'boundary_regions':[]}
    with tempfile.TemporaryDirectory(prefix='jorvik-project-benchmark-') as directory:
        path=Path(directory)/'large.jorvik'
        started=time.perf_counter();save_project(path,geometry,[(batch,'Generated',True)],state)
        save_seconds=time.perf_counter()-started
        del batch
        started=time.perf_counter();manifest,_=load_project(path)
        load_seconds=time.perf_counter()-started
        restored=manifest['areas'][0]['area']
        np.testing.assert_array_equal(restored['coordinates'],points)
        np.testing.assert_array_equal(restored['normals'],-directions)
        np.testing.assert_array_equal(restored['entity_tags'],tags)
        size=path.stat().st_size
    rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {'points':count,'save_seconds':round(save_seconds,3),'load_seconds':round(load_seconds,3),
            'archive_mib':round(size/2**20,2),'peak_process_rss_mib':round(rss/(2**20 if sys.platform=='darwin' else 1024),2),
            'round_trip':'exact coordinates, tags and normals','scope':'backend I/O; excludes viewport restoration'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count',type=int,default=100000)
    print(json.dumps(run(parser.parse_args().count),indent=2))
