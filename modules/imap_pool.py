"""Connexion IMAP persistante (pool a une seule connexion).

Probleme d'origine : chaque action (fetch / delete / archive) ouvrait un
IMAP4_SSL + login + SELECT ce qui coute 1 a 3 secondes. Sur une boite
chargee, cela accumule enormement de latence.

Solution : on garde UNE connexion ouverte, protegee par un lock (IMAP4_SSL
n'est pas thread-safe). On envoie un NOOP toutes les 4 min en arriere plan
pour que le serveur ne ferme pas la session. En cas d'erreur I/O, on ferme
la connexion et la prochaine acquisition la rouvre automatiquement.

Usage :
    pool = ImapPool(email, password)
    with pool.connection() as mail:
        mail.select("INBOX")
        ...
"""
from __future__ import annotations
import imaplib
import threading
import time
from contextlib import contextmanager

from modules.logger import get_logger

_log = get_logger("imap_pool")

_DEFAULT_HOST = "imap.gmail.com"
_DEFAULT_PORT = 993
_KEEPALIVE_INTERVAL = 4 * 60  # 4 min, < 5 min pour eviter NOOP trop lent


class ImapPool:
    def __init__(self, email: str, password: str,
                 host: str = _DEFAULT_HOST, port: int = _DEFAULT_PORT):
        self.email = email
        self.password = password
        self.host = host
        self.port = port
        self._lock = threading.RLock()
        self._mail = None  # imaplib.IMAP4_SSL ou None
        self._last_use = 0.0
        self._stopping = False
        self._keepalive_thread = None

    # ---------------------------------------------------------------
    # API publique
    # ---------------------------------------------------------------
    @contextmanager
    def connection(self):
        """Donne acces a la connexion IMAP. Reconnecte si besoin."""
        self._lock.acquire()
        try:
            mail = self._ensure_connected()
            self._last_use = time.time()
            try:
                yield mail
            except (imaplib.IMAP4.abort, OSError, EOFError) as e:
                _log.warning("IMAP abort/OSError : reconnexion requise : %s", e)
                self._drop_connection()
                raise
        finally:
            self._lock.release()

    def close(self):
        """A appeler a la fermeture de l'application."""
        self._stopping = True
        with self._lock:
            self._drop_connection()

    # ---------------------------------------------------------------
    # Interne
    # ---------------------------------------------------------------
    def _ensure_connected(self):
        if self._mail is not None:
            # Ping rapide pour detecter une connexion cassee
            try:
                typ, _ = self._mail.noop()
                if typ == "OK":
                    return self._mail
            except Exception as e:
                _log.info("NOOP a echoue, on reconnecte : %s", e)
                self._drop_connection()

        # (Re)connexion
        _log.info("Connexion IMAP vers %s:%s...", self.host, self.port)
        try:
            mail = imaplib.IMAP4_SSL(self.host, self.port)
            mail.login(self.email, self.password)
        except Exception as e:
            _log.error("Echec connexion IMAP : %s", e)
            self._mail = None
            raise
        self._mail = mail
        self._start_keepalive()
        return self._mail

    def _drop_connection(self):
        m = self._mail
        self._mail = None
        if m is None:
            return
        try:
            try:
                m.close()
            except Exception:
                pass
            m.logout()
        except Exception:
            pass

    def _start_keepalive(self):
        if self._keepalive_thread and self._keepalive_thread.is_alive():
            return

        def _loop():
            while not self._stopping:
                time.sleep(_KEEPALIVE_INTERVAL)
                if self._stopping:
                    break
                # Si la connexion vient d'etre utilisee, pas besoin de NOOP.
                if time.time() - self._last_use < _KEEPALIVE_INTERVAL / 2:
                    continue
                if not self._lock.acquire(blocking=False):
                    continue
                try:
                    if self._mail is None:
                        continue
                    try:
                        self._mail.noop()
                    except Exception as e:
                        _log.info("Keepalive NOOP echoue : %s", e)
                        self._drop_connection()
                finally:
                    self._lock.release()

        t = threading.Thread(target=_loop, name="imap-keepalive", daemon=True)
        t.start()
        self._keepalive_thread = t
