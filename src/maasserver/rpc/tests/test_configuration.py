# Copyright 2014-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for :py:module:`~maasserver.rpc.configuration`."""

__all__ = []

from urllib.parse import urlparse

from maasserver.models.config import Config
from maasserver.models.signals import bootsources
from maasserver.rpc.configuration import (
    get_archive_mirrors,
    get_proxies,
)
from maasserver.testing.testcase import MAASServerTestCase
from maastesting.factory import factory


class TestGetArchiveMirrors(MAASServerTestCase):

    def setUp(self):
        super(TestGetArchiveMirrors, self).setUp()
        # Disable boot source cache signals.
        self.addCleanup(bootsources.signals.enable)
        bootsources.signals.disable()

    def test_returns_populated_dict_when_main_and_port_is_set(self):
        url = factory.make_parsed_url().geturl()
        Config.objects.set_config("main_archive", url)
        Config.objects.set_config("ports_archive", url)
        self.assertEqual(
            {"main": urlparse(url), "ports": urlparse(url)},
            get_archive_mirrors())


class TestGetProxies(MAASServerTestCase):

    def setUp(self):
        super(TestGetProxies, self).setUp()
        # Disable boot source cache signals.
        self.addCleanup(bootsources.signals.enable)
        bootsources.signals.disable()

    def test_returns_populated_dict_when_http_proxy_is_not_set(self):
        Config.objects.set_config("enable_http_proxy", True)
        Config.objects.set_config("http_proxy", None)
        self.assertEqual(
            {"http": None, "https": None},
            get_proxies())

    def test_returns_populated_dict_when_http_proxy_is_set(self):
        Config.objects.set_config("enable_http_proxy", True)
        url = factory.make_parsed_url().geturl()
        Config.objects.set_config("http_proxy", url)
        self.assertEqual(
            {"http": urlparse(url), "https": urlparse(url)},
            get_proxies())

    def test_returns_populated_dict_when_http_proxy_is_disabled(self):
        Config.objects.set_config("enable_http_proxy", False)
        url = factory.make_parsed_url().geturl()
        Config.objects.set_config("http_proxy", url)
        self.assertEqual(
            {"http": None, "https": None},
            get_proxies())
