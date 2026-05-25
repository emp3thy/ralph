"""Empty package marker.

The skill directory name ``pr-github`` contains a hyphen, so the entry
scripts are loaded by tests via ``importlib.util.spec_from_file_location``
rather than a dotted ``import``. This marker exists so ruff and mypy treat
the directory as a package.
"""
