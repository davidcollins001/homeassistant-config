import sys
import logging


handler = logging.StreamHandler(sys.stderr)
formatter = logging.Formatter(
    '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
handler.setFormatter(formatter)
logging.root.addHandler(handler)
logger = logging.getLogger('rf')
logger.setLevel(logging.INFO)
