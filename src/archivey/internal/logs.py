"""Establish the archivey logger hierarchy. No handlers installed."""

import logging

# Named child loggers - referenced throughout the library
detection = logging.getLogger("archivey.detection")
normalization = logging.getLogger("archivey.normalization")
extraction = logging.getLogger("archivey.extraction")
backends = logging.getLogger("archivey.backends")

# The library never configures handlers, levels, or formatters.
# That is left entirely to the application.
