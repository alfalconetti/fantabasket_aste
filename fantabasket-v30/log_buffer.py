"""
Buffer in memoria per /dev_log.
Modulo separato per garantire che bot.py e dev.py condividano
la stessa istanza della deque (Python cachea i moduli in sys.modules).
"""
import collections
import logging

buffer: collections.deque = collections.deque(maxlen=200)


class _DequeHandler(logging.Handler):
    def emit(self, record):
        buffer.append(self.format(record))


def install():
    """
    Aggiunge il DequeHandler al root logger.
    Va chiamato una sola volta all'avvio da bot.py, dopo basicConfig.
    """
    handler = _DequeHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logging.getLogger().addHandler(handler)
