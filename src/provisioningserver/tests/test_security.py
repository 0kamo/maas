# Copyright 2014-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for MAAS's cluster security module."""

__all__ = []

import binascii
from binascii import b2a_hex
from os import (
    chmod,
    makedirs,
    stat,
)
from os.path import dirname
from unittest.mock import (
    ANY,
    sentinel,
)

from maastesting.factory import factory
from maastesting.matchers import MockCalledOnceWith
from maastesting.testcase import MAASTestCase
from provisioningserver import security
from provisioningserver.utils.fs import (
    FileLock,
    read_text_file,
    write_text_file,
)


class TestGetSharedSecretFromFilesystem(MAASTestCase):

    def write_secret(self):
        secret = factory.make_bytes()
        secret_path = security.get_shared_secret_filesystem_path()
        makedirs(dirname(secret_path), exist_ok=True)
        write_text_file(secret_path, security.to_hex(secret))
        return secret

    def test__returns_None_when_no_secret_exists(self):
        self.assertIsNone(security.get_shared_secret_from_filesystem())

    def test__returns_secret_when_one_exists(self):
        secret = self.write_secret()
        self.assertEqual(
            secret, security.get_shared_secret_from_filesystem())

    def test__same_secret_is_returned_on_subsequent_calls(self):
        self.write_secret()
        self.assertEqual(
            security.get_shared_secret_from_filesystem(),
            security.get_shared_secret_from_filesystem())

    def test__errors_reading_file_are_raised(self):
        self.write_secret()
        secret_path = security.get_shared_secret_filesystem_path()
        self.addCleanup(chmod, secret_path, 0o600)
        chmod(secret_path, 0o000)
        self.assertRaises(IOError, security.get_shared_secret_from_filesystem)

    def test__errors_when_filesystem_value_cannot_be_decoded(self):
        self.write_secret()
        write_text_file(security.get_shared_secret_filesystem_path(), "_")
        self.assertRaises(
            binascii.Error, security.get_shared_secret_from_filesystem)

    def test__deals_fine_with_whitespace_in_filesystem_value(self):
        secret = self.write_secret()
        write_text_file(
            security.get_shared_secret_filesystem_path(),
            " %s\n" % security.to_hex(secret))
        self.assertEqual(secret, security.get_shared_secret_from_filesystem())

    def test__reads_with_lock(self):
        lock = FileLock(security.get_shared_secret_filesystem_path())
        self.assertFalse(lock.is_locked())

        def check_lock(path):
            self.assertTrue(lock.is_locked())
            return "12"  # Two arbitrary hex characters.

        read_text_file = self.patch_autospec(security, "read_text_file")
        read_text_file.side_effect = check_lock
        security.get_shared_secret_from_filesystem()
        self.assertThat(read_text_file, MockCalledOnceWith(ANY))
        self.assertFalse(lock.is_locked())


class TestSetSharedSecretOnFilesystem(MAASTestCase):

    def read_secret(self):
        secret_path = security.get_shared_secret_filesystem_path()
        secret_hex = read_text_file(secret_path)
        return security.to_bin(secret_hex)

    def test__writes_secret(self):
        secret = factory.make_bytes()
        security.set_shared_secret_on_filesystem(secret)
        self.assertEqual(secret, self.read_secret())

    def test__writes_with_lock(self):
        lock = FileLock(security.get_shared_secret_filesystem_path())
        self.assertFalse(lock.is_locked())

        def check_lock(path, data):
            self.assertTrue(lock.is_locked())

        write_text_file = self.patch_autospec(security, "write_text_file")
        write_text_file.side_effect = check_lock
        security.set_shared_secret_on_filesystem(b"foo")
        self.assertThat(write_text_file, MockCalledOnceWith(ANY, ANY))
        self.assertFalse(lock.is_locked())

    def test__writes_with_secure_permissions(self):
        secret = factory.make_bytes()
        security.set_shared_secret_on_filesystem(secret)
        secret_path = security.get_shared_secret_filesystem_path()
        perms_observed = stat(secret_path).st_mode & 0o777
        perms_expected = 0o640
        self.assertEqual(
            perms_expected, perms_observed,
            "Expected %04o, got %04o." % (perms_expected, perms_observed))


class TestInstallSharedSecretScript(MAASTestCase):

    def test__has_add_arguments(self):
        # It doesn't do anything, but it's there to fulfil the contract with
        # ActionScript/MainScript.
        security.InstallSharedSecretScript.add_arguments(sentinel.parser)
        self.assertIsNotNone("Obligatory assertion.")

    def installAndCheckExitCode(self, code):
        error = self.assertRaises(
            SystemExit, security.InstallSharedSecretScript.run, sentinel.args)
        self.assertEqual(code, error.code)

    def test__reads_secret_from_stdin(self):
        secret = factory.make_bytes()

        stdin = self.patch_autospec(security, "stdin")
        stdin.readline.return_value = b2a_hex(secret).decode("ascii")
        stdin.isatty.return_value = False

        self.installAndCheckExitCode(0)
        self.assertEqual(
            secret, security.get_shared_secret_from_filesystem())

    def test__ignores_surrounding_whitespace_from_stdin(self):
        secret = factory.make_bytes()

        stdin = self.patch_autospec(security, "stdin")
        stdin.readline.return_value = (
            " " + b2a_hex(secret).decode("ascii") + " \n")
        stdin.isatty.return_value = False

        self.installAndCheckExitCode(0)
        self.assertEqual(
            secret, security.get_shared_secret_from_filesystem())

    def test__reads_secret_from_tty(self):
        secret = factory.make_bytes()

        stdin = self.patch_autospec(security, "stdin")
        stdin.isatty.return_value = True

        input = self.patch(security, "input")
        input.return_value = b2a_hex(secret).decode("ascii")

        self.installAndCheckExitCode(0)
        self.assertThat(
            input, MockCalledOnceWith("Secret (hex/base16 encoded): "))
        self.assertEqual(
            secret, security.get_shared_secret_from_filesystem())

    def test__ignores_surrounding_whitespace_from_tty(self):
        secret = factory.make_bytes()

        stdin = self.patch_autospec(security, "stdin")
        stdin.isatty.return_value = True

        input = self.patch(security, "input")
        input.return_value = " " + b2a_hex(secret).decode("ascii") + " \n"

        self.installAndCheckExitCode(0)
        self.assertEqual(
            secret, security.get_shared_secret_from_filesystem())

    def test__deals_gracefully_with_eof_from_tty(self):
        stdin = self.patch_autospec(security, "stdin")
        stdin.isatty.return_value = True

        input = self.patch(security, "input")
        input.side_effect = EOFError()

        self.installAndCheckExitCode(1)
        self.assertIsNone(
            security.get_shared_secret_from_filesystem())

    def test__deals_gracefully_with_interrupt_from_tty(self):
        stdin = self.patch_autospec(security, "stdin")
        stdin.isatty.return_value = True

        input = self.patch(security, "input")
        input.side_effect = KeyboardInterrupt()

        self.assertRaises(
            KeyboardInterrupt,
            security.InstallSharedSecretScript.run, sentinel.args)
        self.assertIsNone(
            security.get_shared_secret_from_filesystem())

    def test__prints_error_message_when_secret_cannot_be_decoded(self):
        stdin = self.patch_autospec(security, "stdin")
        stdin.readline.return_value = "garbage"
        stdin.isatty.return_value = False

        print = self.patch(security, "print")

        self.installAndCheckExitCode(1)
        self.assertThat(
            print, MockCalledOnceWith(
                "Secret could not be decoded:", "Odd-length string",
                file=security.stderr))

    def test__prints_message_when_secret_is_installed(self):
        stdin = self.patch_autospec(security, "stdin")
        stdin.readline.return_value = (
            b2a_hex(factory.make_bytes()).decode("ascii"))
        stdin.isatty.return_value = False

        print = self.patch(security, "print")

        self.installAndCheckExitCode(0)
        shared_secret_path = security.get_shared_secret_filesystem_path()
        self.assertThat(
            print, MockCalledOnceWith(
                "Secret installed to %s." % shared_secret_path))


class TestCheckForSharedSecretScript(MAASTestCase):

    def test__has_add_arguments(self):
        # It doesn't do anything, but it's there to fulfil the contract with
        # ActionScript/MainScript.
        security.CheckForSharedSecretScript.add_arguments(sentinel.parser)
        self.assertIsNotNone("Obligatory assertion.")

    def test__exits_non_zero_if_secret_does_not_exist(self):
        print = self.patch(security, "print")
        error = self.assertRaises(
            SystemExit, security.CheckForSharedSecretScript.run, sentinel.args)
        self.assertEqual(1, error.code)
        self.assertThat(
            print, MockCalledOnceWith("Shared-secret is NOT installed."))

    def test__exits_zero_if_secret_exists(self):
        security.set_shared_secret_on_filesystem(factory.make_bytes())
        print = self.patch(security, "print")
        error = self.assertRaises(
            SystemExit, security.CheckForSharedSecretScript.run, sentinel.args)
        self.assertEqual(0, error.code)
        self.assertThat(
            print, MockCalledOnceWith("Shared-secret is installed."))
