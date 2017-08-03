# Copyright 2014-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Test maasserver license key settings views."""

__all__ = []

import http.client

from django.conf import settings
from lxml.html import fromstring
from maasserver import forms
from maasserver.clusterrpc.testing.osystems import (
    make_rpc_osystem,
    make_rpc_release,
)
from maasserver.models import LicenseKey
from maasserver.testing import (
    extract_redirect,
    get_content_links,
)
from maasserver.testing.factory import factory
from maasserver.testing.osystems import patch_usable_osystems
from maasserver.testing.testcase import MAASServerTestCase
from maasserver.utils.django_urls import reverse
from maasserver.utils.orm import reload_object
from maasserver.views import settings as settings_view
from maasserver.views.settings_license_keys import LICENSE_KEY_ANCHOR
from testtools.matchers import ContainsAll


def make_osystem_requiring_license_key(osystem=None, distro_series=None):
    if osystem is None:
        osystem = factory.make_name('osystem')
    if distro_series is None:
        distro_series = factory.make_name('distro_series')
    rpc_release = make_rpc_release(
        distro_series, requires_license_key=True)
    rpc_osystem = make_rpc_osystem(osystem, releases=[rpc_release])
    return rpc_osystem


class LicenseKeyListingTest(MAASServerTestCase):

    def make_license_key_with_os(self, osystem=None, distro_series=None,
                                 license_key=None):
        license_key = factory.make_LicenseKey(
            osystem=osystem, distro_series=distro_series,
            license_key=license_key)
        osystem = make_osystem_requiring_license_key(
            license_key.osystem, license_key.distro_series)
        return license_key, osystem

    def make_license_keys(self, count):
        keys = []
        osystems = []
        for _ in range(count):
            key, osystem = self.make_license_key_with_os()
            keys.append(key)
            osystems.append(osystem)
        patch_usable_osystems(self, osystems=osystems)
        self.patch(
            settings_view,
            'gen_all_known_operating_systems').return_value = osystems
        return keys, osystems

    def test_settings_contains_osystem_and_distro_series(self):
        self.client_log_in(as_admin=True)
        keys, _ = self.make_license_keys(3)
        response = self.client.get(reverse('settings'))
        os_titles = [key.osystem for key in keys]
        series_titles = [key.distro_series for key in keys]
        self.assertThat(
            response.content.decode(settings.DEFAULT_CHARSET),
            ContainsAll([title for title in os_titles + series_titles]))

    def test_settings_link_to_add_license_key(self):
        self.client_log_in(as_admin=True)
        self.make_license_keys(3)
        links = get_content_links(self.client.get(reverse('settings')))
        script_add_link = reverse('license-key-add')
        self.assertIn(script_add_link, links)

    def test_settings_contains_links_to_delete(self):
        self.client_log_in(as_admin=True)
        keys, _ = self.make_license_keys(3)
        links = get_content_links(self.client.get(reverse('settings')))
        license_key_delete_links = [
            reverse(
                'license-key-delete', args=[key.osystem, key.distro_series])
            for key in keys]
        self.assertThat(links, ContainsAll(license_key_delete_links))

    def test_settings_contains_links_to_edit(self):
        self.client_log_in(as_admin=True)
        keys, _ = self.make_license_keys(3)
        links = get_content_links(self.client.get(reverse('settings')))
        license_key_delete_links = [
            reverse(
                'license-key-edit', args=[key.osystem, key.distro_series])
            for key in keys]
        self.assertThat(links, ContainsAll(license_key_delete_links))

    def test_settings_contains_commissioning_scripts_slot_anchor(self):
        self.client_log_in(as_admin=True)
        self.make_license_keys(3)
        response = self.client.get(reverse('settings'))
        document = fromstring(response.content)
        slots = document.xpath(
            "//div[@id='%s']" % LICENSE_KEY_ANCHOR)
        self.assertEqual(
            1, len(slots),
            "Missing anchor '%s'" % LICENSE_KEY_ANCHOR)


class LicenseKeyAddTest(MAASServerTestCase):

    def test_can_create_license_key(self):
        self.client_log_in(as_admin=True)
        osystem = make_osystem_requiring_license_key()
        patch_usable_osystems(self, osystems=[osystem])
        self.patch(forms, 'validate_license_key').return_value = True
        series = osystem['default_release']
        key = factory.make_name('key')
        add_link = reverse('license-key-add')
        definition = {
            'osystem': osystem['name'],
            'distro_series': "%s/%s" % (osystem['name'], series),
            'license_key': key,
            }
        response = self.client.post(add_link, definition)
        self.assertEqual(
            (http.client.FOUND, reverse('settings')),
            (response.status_code, extract_redirect(response)))
        new_license_key = LicenseKey.objects.get(
            osystem=osystem['name'], distro_series=series)
        expected_result = {
            'osystem': osystem['name'],
            'distro_series': series,
            'license_key': key,
            }
        self.assertAttributes(new_license_key, expected_result)


class LicenseKeyEditTest(MAASServerTestCase):

    def test_can_update_license_key(self):
        self.client_log_in(as_admin=True)
        key = factory.make_LicenseKey()
        osystem = make_osystem_requiring_license_key(
            key.osystem, key.distro_series)
        patch_usable_osystems(self, osystems=[osystem])
        self.patch(forms, 'validate_license_key').return_value = True
        new_key = factory.make_name('key')
        edit_link = reverse(
            'license-key-edit', args=[key.osystem, key.distro_series])
        definition = {
            'osystem': key.osystem,
            'distro_series': "%s/%s" % (key.osystem, key.distro_series),
            'license_key': new_key,
            }
        response = self.client.post(edit_link, definition)
        self.assertEqual(
            (http.client.FOUND, reverse('settings')),
            (response.status_code, extract_redirect(response)))
        expected_result = {
            'osystem': key.osystem,
            'distro_series': key.distro_series,
            'license_key': new_key,
            }
        self.assertAttributes(reload_object(key), expected_result)


class LicenseKeyDeleteTest(MAASServerTestCase):

    def test_can_delete_license_key(self):
        self.client_log_in(as_admin=True)
        key = factory.make_LicenseKey()
        delete_link = reverse(
            'license-key-delete', args=[key.osystem, key.distro_series])
        response = self.client.post(delete_link, {'post': 'yes'})
        self.assertEqual(
            (http.client.FOUND, reverse('settings')),
            (response.status_code, extract_redirect(response)))
        self.assertFalse(
            LicenseKey.objects.filter(
                osystem=key.osystem, distro_series=key.distro_series).exists())
