# Copyright 2014-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for the `LicenseKey` API."""

__all__ = []

import http.client

from maasserver import forms
from maasserver.clusterrpc.testing.osystems import (
    make_rpc_osystem,
    make_rpc_release,
)
from maasserver.models.licensekey import LicenseKey
from maasserver.testing.api import APITestCase
from maasserver.testing.factory import factory
from maasserver.testing.osystems import patch_usable_osystems
from maasserver.utils.converters import json_load_bytes
from maasserver.utils.django_urls import reverse
from maasserver.utils.orm import (
    get_one,
    reload_object,
)


class TestLicenseKey(APITestCase.ForUser):

    def get_url(self, osystem, distro_series):
        """Return the URL for the license key of the given osystem and
        distro_series."""
        return reverse('license_key_handler', args=[osystem, distro_series])

    def make_license_key_with_os(self, license_key=None):
        release = make_rpc_release(requires_license_key=True)
        osystem = make_rpc_osystem(releases=[release])
        patch_usable_osystems(self, osystems=[osystem])
        license_key = factory.make_LicenseKey(
            osystem=osystem['name'], distro_series=release['name'],
            license_key=license_key)
        return license_key

    def test_handler_path(self):
        osystem = factory.make_name('osystem')
        distro_series = factory.make_name('series')
        self.assertEqual(
            '/api/2.0/license-key/%s/%s' % (osystem, distro_series),
            self.get_url(osystem, distro_series))

    def test_POST_is_prohibited(self):
        self.become_admin()
        license_key = factory.make_LicenseKey()
        response = self.client.post(
            self.get_url(license_key.osystem, license_key.distro_series),
            {'osystem': "New osystem"})
        self.assertEqual(http.client.METHOD_NOT_ALLOWED, response.status_code)

    def test_GET_returns_license_key(self):
        self.become_admin()
        license_key = factory.make_LicenseKey()

        response = self.client.get(
            self.get_url(license_key.osystem, license_key.distro_series))
        self.assertEqual(http.client.OK, response.status_code)

        parsed_result = json_load_bytes(response.content)
        self.assertEqual(
            (
                license_key.osystem,
                license_key.distro_series,
                license_key.license_key,
            ),
            (
                parsed_result['osystem'],
                parsed_result['distro_series'],
                parsed_result['license_key'],
            ))

    def test_GET_returns_404_for_unknown_os_and_series(self):
        self.become_admin()
        self.assertEqual(
            http.client.NOT_FOUND,
            self.client.get(self.get_url('noneos', 'noneseries')).status_code)

    def test_GET_requires_admin(self):
        license_key = factory.make_LicenseKey()
        response = self.client.get(
            self.get_url(license_key.osystem, license_key.distro_series))
        self.assertEqual(http.client.FORBIDDEN, response.status_code)

    def test_PUT_updates_license_key(self):
        self.become_admin()
        license_key = self.make_license_key_with_os()
        self.patch_autospec(forms, 'validate_license_key').return_value = True
        new_key = factory.make_name('key')
        new_values = {
            'license_key': new_key,
            }

        response = self.client.put(
            self.get_url(
                license_key.osystem, license_key.distro_series),
            new_values)
        self.assertEqual(http.client.OK, response.status_code)
        license_key = reload_object(license_key)
        self.assertEqual(new_key, license_key.license_key)

    def test_PUT_requires_admin(self):
        key = factory.make_name('key')
        license_key = self.make_license_key_with_os(license_key=key)
        response = self.client.put(
            self.get_url(license_key.osystem, license_key.distro_series),
            {'license_key': factory.make_name('key')})
        self.assertEqual(http.client.FORBIDDEN, response.status_code)
        self.assertEqual(key, reload_object(license_key).license_key)

    def test_PUT_returns_404_for_unknown_os_and_series(self):
        self.become_admin()
        self.assertEqual(
            http.client.NOT_FOUND,
            self.client.put(
                self.get_url(
                    'noneos', 'noneseries')).status_code)

    def test_DELETE_deletes_license_key(self):
        self.become_admin()
        license_key = factory.make_LicenseKey()
        response = self.client.delete(
            self.get_url(license_key.osystem, license_key.distro_series))
        self.assertEqual(http.client.NO_CONTENT, response.status_code)
        self.assertIsNone(reload_object(license_key))

    def test_DELETE_requires_admin(self):
        license_key = factory.make_LicenseKey()
        response = self.client.delete(
            self.get_url(license_key.osystem, license_key.distro_series))
        self.assertEqual(http.client.FORBIDDEN, response.status_code)
        self.assertIsNotNone(reload_object(license_key))

    def test_DELETE_is_idempotent(self):
        osystem = factory.make_name('no-os')
        series = factory.make_name('no-series')
        self.become_admin()
        response1 = self.client.delete(self.get_url(osystem, series))
        response2 = self.client.delete(self.get_url(osystem, series))
        self.assertEqual(response1.status_code, response2.status_code)


class TestLicenseKeysAPI(APITestCase.ForUser):

    def test_handler_path(self):
        self.assertEqual(
            '/api/2.0/license-keys/', reverse('license_keys_handler'))

    def test_GET_returns_license_keys(self):
        self.become_admin()
        orig_license_key = factory.make_LicenseKey()

        response = self.client.get(reverse('license_keys_handler'))
        self.assertEqual(
            http.client.OK, response.status_code, response.content)

        parsed_result = json_load_bytes(response.content)
        self.assertEqual(1, len(parsed_result))
        [returned_network] = parsed_result
        fields = {'osystem', 'distro_series', 'license_key'}
        self.assertItemsEqual(
            fields.union({'resource_uri'}),
            returned_network.keys())
        expected_values = {
            field: getattr(orig_license_key, field)
            for field in fields
            if field != 'resource_uri'
        }
        expected_values['resource_uri'] = reverse(
            'license_key_handler',
            args=[orig_license_key.osystem, orig_license_key.distro_series])
        self.assertEqual(expected_values, returned_network)

    def test_GET_requires_admin(self):
        factory.make_LicenseKey()
        response = self.client.get(reverse('license_keys_handler'))
        self.assertEqual(http.client.FORBIDDEN, response.status_code)

    def test_GET_returns_empty_if_no_networks(self):
        self.become_admin()
        response = self.client.get(reverse('license_keys_handler'))
        self.assertEqual(
            http.client.OK, response.status_code, response.content)
        self.assertEqual([], json_load_bytes(response.content))

    def test_POST_creates_license_key(self):
        self.become_admin()
        release = make_rpc_release(requires_license_key=True)
        osystem = make_rpc_osystem(releases=[release])
        patch_usable_osystems(self, osystems=[osystem])
        self.patch_autospec(forms, 'validate_license_key').return_value = True
        params = {
            'osystem': osystem['name'],
            'distro_series': release['name'],
            'license_key': factory.make_name('key'),
        }
        response = self.client.post(reverse('license_keys_handler'), params)
        self.assertEqual(http.client.OK, response.status_code)
        license_key = LicenseKey.objects.get(
            osystem=params['osystem'], distro_series=params['distro_series'])
        self.assertAttributes(license_key, params)

    def test_POST_supports_combined_distro_series(self):
        # API allows specifying only distro_series containing both
        # os and series in the "os/series" form.
        self.become_admin()
        release = make_rpc_release(requires_license_key=True)
        osystem = make_rpc_osystem(releases=[release])
        patch_usable_osystems(self, osystems=[osystem])
        self.patch_autospec(forms, 'validate_license_key').return_value = True
        params = {
            'distro_series': "%s/%s" % (osystem['name'], release['name']),
            'license_key': factory.make_name('key'),
        }
        response = self.client.post(reverse('license_keys_handler'), params)
        self.assertEqual(http.client.OK, response.status_code)
        license_key = LicenseKey.objects.get(
            osystem=osystem['name'], distro_series=release['name'])
        expected_params = {
            'osystem': osystem['name'],
            'distro_series': release['name'],
            'license_key': params['license_key'],
        }
        self.assertAttributes(license_key, expected_params)

    def test_POST_requires_osystem(self):
        # If osystem is not specified and distro_series is not in the
        # osystem/release form, API call fails.
        self.become_admin()
        release = make_rpc_release(requires_license_key=True)
        osystem = make_rpc_osystem(releases=[release])
        patch_usable_osystems(self, osystems=[osystem])
        self.patch_autospec(forms, 'validate_license_key').return_value = True
        params = {
            'distro_series': release['name'],
            'license_key': factory.make_name('key'),
        }
        response = self.client.post(reverse('license_keys_handler'), params)
        self.assertEqual(http.client.BAD_REQUEST, response.status_code)

    def test_POST_requires_admin(self):
        osystem = factory.make_name('no-os')
        series = factory.make_name('no-series')
        response = self.client.post(
            reverse('license_keys_handler'),
            {'osystem': osystem, 'distro_series': series})
        self.assertEqual(http.client.FORBIDDEN, response.status_code)
        self.assertIsNone(
            get_one(LicenseKey.objects.filter(
                osystem=osystem, distro_series=series)))
