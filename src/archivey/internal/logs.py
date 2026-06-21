"""The archivey logger hierarchy — the single source of truth for logger names.

Modules import the named logger they need from here (e.g.
``from archivey.internal.logs import normalization as logger``) rather than calling
``logging.getLogger("archivey.normalization")`` with a hand-typed string, so the
hierarchy is defined in exactly one place and importing any module establishes it.

The library never installs handlers, levels, or formatters — that is left entirely to
the application (see the ``logging`` spec).
"""

import logging

detection = logging.getLogger("archivey.detection")
normalization = logging.getLogger("archivey.normalization")
extraction = logging.getLogger("archivey.extraction")
backends = logging.getLogger("archivey.backends")
streams = logging.getLogger("archivey.streams")
# Used by the decompressed-output digest verification stage to warn when an expected
# digest cannot be checked (unknown algorithm, or its backend is not installed).
integrity = logging.getLogger("archivey.integrity")
