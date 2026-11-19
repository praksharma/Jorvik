"""Lightweight method catalogue shared by the GUI and sampling worker."""
METHODS = {
    'random': 'Uniform random', 'grid': 'Regular grid', 'lhs': 'Latin hypercube (LHS)',
    'sobol': 'Sobol', 'halton': 'Halton', 'hammersley': 'Hammersley',
    'stratified': 'Stratified',
}
RESOLUTIONS = (16, 32, 64)
