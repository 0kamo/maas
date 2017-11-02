# Copyright 2014-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Utilities for executing external commands."""

__all__ = [
    'call_and_check',
    'ExternalProcessError',
    'pipefork',
    'PipeForkError',
    'select_c_utf8_bytes_locale',
    'select_c_utf8_locale',
]

from contextlib import contextmanager
import os
import pickle
from pipes import quote
import signal
from string import printable
from subprocess import (
    CalledProcessError,
    PIPE,
    Popen,
)
from sys import (
    stderr,
    stdout,
)
from tempfile import TemporaryFile

from twisted.python.failure import Failure

# A mapping of signal numbers to names. It is strange that this isn't in the
# standard library (but I did check).
signal_names = {
    value: name for name, value in vars(signal).items()
    if name.startswith('SIG') and '_' not in name
}

# A table suitable for use with str.translate() to replace each
# non-printable and non-ASCII character in a byte string with a question
# mark, mimicking the "replace" strategy when encoding and decoding.
non_printable_replace_table = "".join(
    chr(i) if chr(i) in printable else "?"
    for i in range(0xff + 0x01)).encode("ascii")


class ExternalProcessError(CalledProcessError):
    """Raised when there's a problem calling an external command.

    Unlike `CalledProcessError`:

    - `__str__()` returns a string containing the output of the failed
      external process, if available, and tries to keep in valid Unicode
      characters from the error message.

    """

    @classmethod
    def upgrade(cls, error):
        """Upgrade the given error to an instance of this class.

        If `error` is an instance of :py:class:`CalledProcessError`, this will
        change its class, in-place, to :py:class:`ExternalProcessError`.

        There are two ways we could have done this:

        1. Change the class of `error` in-place.

        2. Capture ``exc_info``, create a new exception based on `error`, then
           re-raise with the 3-argument version of ``raise``.

        #1 seems a lot simpler so that's what this method does. The caller
        needs then only use a naked ``raise`` to get the utility of this class
        without losing the traceback.
        """
        if type(error) is CalledProcessError:
            error.__class__ = cls

    @staticmethod
    def _to_unicode(string):
        if isinstance(string, bytes):
            return string.decode("ascii", "replace")
        else:
            return str(string)

    @staticmethod
    def _to_ascii(string, table=non_printable_replace_table):
        if isinstance(string, bytes):
            return string.translate(table)
        elif isinstance(string, str):
            return string.encode("ascii", "replace").translate(table)
        else:
            return str(string).encode("ascii", "replace").translate(table)

    def __str__(self):
        cmd = " ".join(quote(self._to_unicode(part)) for part in self.cmd)
        output = self._to_unicode(self.output)
        return "Command `%s` returned non-zero exit status %d:\n%s" % (
            cmd, self.returncode, output)

    @property
    def output_as_ascii(self):
        """The command's output as printable ASCII.

        Non-printable and non-ASCII characters are filtered out.
        """
        return self._to_ascii(self.output)

    @property
    def output_as_unicode(self):
        """The command's output as Unicode text.

        Invalid Unicode characters are filtered out.
        """
        return self._to_unicode(self.output)


def call_and_check(command, *args, **kwargs):
    """Execute a command, similar to `subprocess.check_call()`.

    :param command: Command line, as a list of strings.
    :return: The command's output from standard output.
    :raise ExternalProcessError: If the command returns nonzero.
    """
    process = Popen(command, *args, stdout=PIPE, stderr=PIPE, **kwargs)
    stdout, stderr = process.communicate()
    stderr = stderr.strip()
    if process.returncode != 0:
        raise ExternalProcessError(process.returncode, command, output=stderr)
    return stdout


class PipeForkError(Exception):
    """An error occurred in `pipefork`."""


@contextmanager
def pipefork():
    """Context manager that forks with pipes between parent and child.

    Use like so::

        with pipefork() as (pid, fin, fout):
            if pid == 0:
                # This is the child.
                ...
            else:
                # This is the parent.
                ...

    Pipes are set up so that the parent can write to the child, and
    vice-versa.

    In the child, ``fin`` is a file that reads from the parent, and ``fout``
    is a file that writes to the parent.

    In the parent, ``fin`` is a file that reads from the child, and ``fout``
    is a file that writes to the child.

    Be careful to think about closing these file objects to avoid deadlocks.
    For example, the following will deadlock:

        with pipefork() as (pid, fin, fout):
            if pid == 0:
                fin.read()  # Read from the parent.
                fout.write(b'Moien')  # Greet the parent.
            else:
                fout.write(b'Hello')  # Greet the child.
                fin.read()  # Read from the child *BLOCKS FOREVER*

    The reason is that the read in the child never returns because the pipe is
    never closed. Closing ``fout`` in the parent resolves the problem::

        with pipefork() as (pid, fin, fout):
            if pid == 0:
                fin.read()  # Read from the parent.
                fout.write(b'Moien')  # Greet the parent.
            else:
                fout.write(b'Hello')  # Greet the child.
                fout.close()  # Close the write pipe to the child.
                fin.read()  # Read from the child.

    Exceptions raised in the child are magically re-raised in the parent. When
    the child has died for another reason, a signal perhaps, a `PipeForkError`
    is raised with an explanatory message.

    Signal handlers in the child are NOT modified. This means that signal
    handlers set in the parent will still be present in the child.

    :raises: `PipeForkError` when the child process dies a somewhat unnatural
        death, e.g. by a signal or when writing a crash-dump fails.
    """
    crashfile = TemporaryFile()

    c2pread, c2pwrite = os.pipe()
    p2cread, p2cwrite = os.pipe()

    pid = os.fork()

    if pid == 0:
        # Child: this conditional branch runs in the child process.
        try:
            os.close(c2pread)
            os.close(p2cwrite)

            with os.fdopen(p2cread, 'rb') as fin:
                with os.fdopen(c2pwrite, 'wb') as fout:
                    yield pid, fin, fout

            stdout.flush()
            stderr.flush()
        except SystemExit as se:
            # Exit hard, not soft.
            os._exit(se.code)
        except:
            try:
                # Pickle error to crash file.
                pickle.dump(Failure(), crashfile, pickle.HIGHEST_PROTOCOL)
                crashfile.flush()
            finally:
                # Exit hard.
                os._exit(2)
        finally:
            # Exit hard.
            os._exit(0)
    else:
        # Parent: this conditional branch runs in the parent process.
        os.close(c2pwrite)
        os.close(p2cread)

        with os.fdopen(c2pread, 'rb') as fin:
            with os.fdopen(p2cwrite, 'wb') as fout:
                yield pid, fin, fout

        # Wait for the child to finish.
        _, status = os.waitpid(pid, 0)
        signal = (status & 0xff)
        code = (status >> 8) & 0xff

        # Check for a saved crash.
        crashfile.seek(0)
        try:
            error = pickle.load(crashfile)
        except EOFError:
            # No crash was recorded.
            error = None
        else:
            # Raise exception from child.
            error.raiseException()
        finally:
            crashfile.close()

        if os.WIFSIGNALED(status):
            # The child was killed by a signal.
            raise PipeForkError(
                "Child killed by signal %d (%s)" % (
                    signal, signal_names.get(signal, "?")))
        elif code != 0:
            # The child exited with a non-zero code.
            raise PipeForkError(
                "Child exited with code %d" % code)
        else:
            # All okay.
            pass


@contextmanager
def objectfork():
    """Like `pipefork`, but objects can be passed between parent and child.

    Usage::

        with objectfork() as (pid, recv, send):
            if pid == 0:
                # Child.
                for foo in bar():
                    send(foo)
                send(None)  # Done.
            else:
                for data in iter(recv, None):
                    ...  # Process data.

    In the child, ``recv`` receives objects sent -- via `send` -- from
    the parent.

    In the parent, ``recv`` receives objects sent -- via `send` -- from
    the child.

    All objects must be picklable.

    See `pipefork` for more details.
    """
    with pipefork() as (pid, fin, fout):

        def recv():
            return pickle.load(fin)

        def send(obj):
            pickle.dump(obj, fout, pickle.HIGHEST_PROTOCOL)
            fout.flush()  # cPickle.dump() does not flush.

        yield pid, recv, send


def has_command_available(command):
    """Return True if `command` is available on the system."""
    try:
        call_and_check(["which", command])
    except ExternalProcessError:
        return False
    return True


def select_c_utf8_locale(environ=os.environ):
    """Return a dict containing an environment that uses the C.UTF-8 locale.

    C.UTF-8 is the new en_US.UTF-8, i.e. it's the new default locale when no
    other locale makes sense.

    This function takes a starting environment, by default that of the current
    process, strips away all locale and language settings (i.e. LC_* and LANG)
    and selects C.UTF-8 in their place.

    :param environ: A base environment to start from. By default this is
        ``os.environ``. It will not be modified.
    """
    environ = {
        name: value for name, value in environ.items()
        if not name.startswith('LC_')
    }
    environ.update({
        'LC_ALL': 'C.UTF-8',
        'LANG': 'C.UTF-8',
        'LANGUAGE': 'C.UTF-8',
    })
    return environ


def select_c_utf8_bytes_locale(environ=os.environb):
    """Return a dict containing an environment that uses the C.UTF-8 locale.

    C.UTF-8 is the new en_US.UTF-8, i.e. it's the new default locale when no
    other locale makes sense.

    This function takes a starting environment, by default that of the current
    process, strips away all locale and language settings (i.e. LC_* and LANG)
    and selects C.UTF-8 in their place.

    :param environ: A base environment to start from. By default this is
        ``os.environb``. It will not be modified.
    """
    environ = {
        name: value for name, value in environ.items()
        if not name.startswith(b'LC_')
    }
    environ.update({
        b'LC_ALL': b'C.UTF-8',
        b'LANG': b'C.UTF-8',
        b'LANGUAGE': b'C.UTF-8',
    })
    return environ
