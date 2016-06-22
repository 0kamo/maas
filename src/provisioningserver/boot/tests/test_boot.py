# Copyright 2014-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for `provisioningserver.boot`."""

__all__ = []

import errno
import os
from unittest import mock
from urllib.parse import urlparse

from fixtures import EnvironmentVariableFixture
from maastesting.factory import factory
from maastesting.matchers import MockCalledOnceWith
from maastesting.testcase import (
    MAASTestCase,
    MAASTwistedRunTest,
)
from provisioningserver import boot
from provisioningserver.boot import (
    BootMethod,
    BytesReader,
    gen_template_filenames,
    get_main_archive_url,
    get_ports_archive_url,
    get_remote_mac,
)
from provisioningserver.rpc import region
from provisioningserver.rpc.testing import MockLiveClusterToRegionRPCFixture
import tempita
from twisted.internet.defer import (
    inlineCallbacks,
    succeed,
)
from twisted.python import context


class FakeBootMethod(BootMethod):

    name = "fake"
    bios_boot_method = "fake"
    template_subdir = "fake"
    bootloader_path = "fake.efi"
    arch_octet = "00:00"

    def match_path(self, backend, path):
        return {}

    def get_reader(backend, kernel_params, **extra):
        return BytesReader("")

    def install_bootloader():
        pass


class TestBootMethod(MAASTestCase):
    """Test for `BootMethod` in `provisioningserver.boot`."""

    run_tests_with = MAASTwistedRunTest.make_factory(timeout=5)

    @inlineCallbacks
    def test_get_remote_mac(self):
        remote_host = factory.make_ipv4_address()
        call_context = {
            "local": (
                factory.make_ipv4_address(),
                factory.pick_port()),
            "remote": (
                remote_host,
                factory.pick_port()),
            }

        mock_find = self.patch(boot, 'find_mac_via_arp')
        yield context.call(call_context, get_remote_mac)
        self.assertThat(mock_find, MockCalledOnceWith(remote_host))

    def test_gen_template_filenames(self):
        purpose = factory.make_name("purpose")
        arch, subarch = factory.make_names("arch", "subarch")
        expected = [
            "config.%s.%s.%s.template" % (purpose, arch, subarch),
            "config.%s.%s.template" % (purpose, arch),
            "config.%s.template" % (purpose, ),
            "config.template",
            ]
        observed = gen_template_filenames(purpose, arch, subarch)
        self.assertSequenceEqual(expected, list(observed))

    def test_get_pxe_template(self):
        method = FakeBootMethod()
        purpose = factory.make_name("purpose")
        arch, subarch = factory.make_names("arch", "subarch")
        filename = factory.make_name("filename")
        # Set up the mocks that we've patched in.
        gen_filenames = self.patch(boot, "gen_template_filenames")
        gen_filenames.return_value = [filename]
        from_filename = self.patch(tempita.Template, "from_filename")
        from_filename.return_value = mock.sentinel.template
        # The template returned matches the return value above.
        template = method.get_template(purpose, arch, subarch)
        self.assertEqual(mock.sentinel.template, template)
        # gen_pxe_template_filenames is called to obtain filenames.
        gen_filenames.assert_called_once_with(purpose, arch, subarch)
        # Tempita.from_filename is called with an absolute path derived from
        # the filename returned from gen_pxe_template_filenames.
        from_filename.assert_called_once_with(
            os.path.join(method.get_template_dir(), filename),
            encoding="UTF-8")

    def make_fake_templates_dir(self, method):
        """Set up a fake templates dir, and return its path."""
        fake_root = self.make_dir()
        fake_etc_maas = os.path.join(fake_root, "etc", "maas")
        self.useFixture(EnvironmentVariableFixture('MAAS_ROOT', fake_root))
        fake_templates = os.path.join(
            fake_etc_maas, 'templates/%s' % method.template_subdir)
        os.makedirs(fake_templates)
        return fake_templates

    def test_get_template_gets_default_if_available(self):
        # If there is no template matching the purpose, arch, and subarch,
        # but there is a completely generic template, then get_pxe_template()
        # falls back to that as the default.
        method = FakeBootMethod()
        templates_dir = self.make_fake_templates_dir(method)
        generic_template = factory.make_file(templates_dir, 'config.template')
        purpose = factory.make_name("purpose")
        arch, subarch = factory.make_names("arch", "subarch")
        self.assertEqual(
            generic_template,
            method.get_template(purpose, arch, subarch).name)

    def test_get_template_not_found(self):
        # It is a critical and unrecoverable error if the default template
        # is not found.
        method = FakeBootMethod()
        self.make_fake_templates_dir(method)
        self.assertRaises(
            AssertionError, method.get_template,
            *factory.make_names("purpose", "arch", "subarch"))

    def test_get_templates_only_suppresses_ENOENT(self):
        # The IOError arising from trying to load a template that doesn't
        # exist is suppressed, but other errors are not.
        method = FakeBootMethod()
        from_filename = self.patch(tempita.Template, "from_filename")
        from_filename.side_effect = IOError()
        from_filename.side_effect.errno = errno.EACCES
        self.assertRaises(
            IOError, method.get_template,
            *factory.make_names("purpose", "arch", "subarch"))


class TestGetArchiveUrl(MAASTestCase):

    run_tests_with = MAASTwistedRunTest.make_factory(timeout=5)

    def patch_rpc_methods(self, return_value=None):
        fixture = self.useFixture(MockLiveClusterToRegionRPCFixture())
        protocol, connecting = fixture.makeEventLoop(region.GetArchiveMirrors)
        protocol.GetArchiveMirrors.return_value = return_value
        return protocol, connecting

    @inlineCallbacks
    def test_get_main_archive_url(self):
        mirrors = {
            'main': urlparse(factory.make_url('ports')),
            'ports': urlparse(factory.make_url('ports')),
        }
        return_value = succeed(mirrors)
        protocol, connecting = self.patch_rpc_methods(return_value)
        self.addCleanup((yield connecting))
        value = yield get_main_archive_url()
        expected_url = mirrors['main'].geturl()
        self.assertEqual(expected_url, value)

    @inlineCallbacks
    def test_get_ports_archive_url(self):
        mirrors = {
            'main': urlparse(factory.make_url('ports')),
            'ports': urlparse(factory.make_url('ports')),
        }
        return_value = succeed(mirrors)
        protocol, connecting = self.patch_rpc_methods(return_value)
        self.addCleanup((yield connecting))
        value = yield get_ports_archive_url()
        expected_url = mirrors['ports'].geturl()
        self.assertEqual(expected_url, value)
