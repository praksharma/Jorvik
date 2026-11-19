"""Shared source loading for display and sampling; independent of Qt."""
from pathlib import Path
import os
import re
import sys

FORMATS = {'.geo': 'geo', '.step': 'step', '.stp': 'step',
           '.brep': 'brep', '.iges': 'iges', '.igs': 'iges'}
FILE_FILTER = 'Supported geometry (*.geo *.step *.stp *.brep *.iges *.igs);;Gmsh (*.geo);;STEP (*.step *.stp);;Open CASCADE BREP (*.brep);;IGES (*.iges *.igs)'
DEFAULT_UNITS = 'Source geometry units (unspecified)'

_STEP_SI_PREFIX = {
    '.MILLI.': 'mm',
    '.CENTI.': 'cm',
    '$': 'm',
}
_IGES_UNIT_NAME = {
    'MM': 'mm', 'MILLIMETER': 'mm', 'MILLIMETERS': 'mm',
    'CM': 'cm', 'CENTIMETER': 'cm', 'CENTIMETERS': 'cm',
    'M': 'm', 'METER': 'm', 'METERS': 'm', 'METRE': 'm', 'METRES': 'm',
    'IN': 'in', 'INCH': 'in', 'INCHES': 'in',
    'FT': 'ft', 'FEET': 'ft', 'FOOT': 'ft',
}


def detect_coordinate_units(filename, source_format=None):
    """Detect unambiguous declared CAD length units without rescaling coordinates."""
    path = Path(filename)
    kind = source_format or FORMATS.get(path.suffix.lower())
    if kind not in ('step', 'iges') or not path.is_file():
        return DEFAULT_UNITS
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return DEFAULT_UNITS
    found = set()
    if kind == 'step':
        upper = text.upper()
        for match in re.finditer(r'SI_UNIT\s*\(\s*(\.[A-Z]+\.|\$)\s*,\s*\.METRE\.\s*\)', upper):
            unit = _STEP_SI_PREFIX.get(match.group(1))
            if unit:
                found.add(unit)
        if re.search(r"CONVERSION_BASED_UNIT\s*\(\s*'INCH'", upper):
            found.add('in')
        elif re.search(r"CONVERSION_BASED_UNIT\s*\(\s*'FOOT'", upper):
            found.add('ft')
        if len(found) == 1:
            return next(iter(found))
        return DEFAULT_UNITS
    # IGES Global section: inspect lines with 'G' in column 73 (or standard Hollerith unit tokens)
    global_lines = [line[:72] for line in text.splitlines() if len(line) >= 73 and line[72] == 'G']
    header = ''.join(global_lines) if global_lines else text[:4096]
    for match in re.finditer(r',\s*\d+H([A-Za-z]+)\s*,', header):
        token = match.group(1).upper()
        if token in _IGES_UNIT_NAME:
            found.add(_IGES_UNIT_NAME[token])
    if len(found) == 1:
        return next(iter(found))
    return DEFAULT_UNITS


def open_source(gmsh, filename):
    """Open scripts in their own kernel, neutral CAD explicitly through OCC."""
    path = Path(filename).resolve()
    kind = FORMATS.get(path.suffix.lower())
    if kind is None:
        raise ValueError('Choose a Gmsh .geo, STEP, Open CASCADE .brep or IGES geometry file.')
    if not path.is_file():
        raise ValueError('Geometry file does not exist.')
    if kind == 'geo':
        gmsh.open(str(path))
    else:
        gmsh.model.add(path.stem)
        # OCC can print directly to native stdout despite General.Terminal=0.
        # Workers reserve stdout for JSON: retain native diagnostics on stderr.
        sys.stdout.flush()
        saved_stdout = os.dup(1)
        try:
            os.dup2(2, 1)
            gmsh.model.occ.importShapes(str(path), highestDimOnly=False, format=kind)
        finally:
            os.dup2(saved_stdout, 1)
            os.close(saved_stdout)
        gmsh.model.occ.synchronize()
    return kind


def import_options(gmsh, source_format):
    """Record Jorvik's loader arguments, not inferred CAD exporter settings."""
    options = {'backend': 'gmsh', 'backend_version': gmsh.__version__,
               'read_config_files': False,
               'loader': 'open' if source_format == 'geo' else 'occ.importShapes'}
    if source_format != 'geo':
        options.update(highest_dim_only=False, format=source_format)
    return options


def validate_import_metadata(record):
    """Older records may omit provenance; never invent their import settings."""
    kind = record.get('source_format')
    if kind is not None and (not isinstance(kind, str) or kind not in FORMATS.values()):
        raise ValueError('Invalid source format metadata.')
    if 'import_options' not in record:
        return
    options = record['import_options']
    if (not isinstance(options, dict) or kind is None
            or options.get('backend') != 'gmsh'
            or not isinstance(options.get('backend_version'), str)
            or not options['backend_version'].strip()
            or options.get('read_config_files') is not False
            or options.get('loader') != ('open' if kind == 'geo' else 'occ.importShapes')):
        raise ValueError('Invalid geometry import options.')
    if kind != 'geo' and (options.get('highest_dim_only') is not False or options.get('format') != kind):
        raise ValueError('Invalid CAD import options.')
