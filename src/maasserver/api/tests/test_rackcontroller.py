# Copyright 2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for the Rack Controller API."""

import http.client

from django.core.urlresolvers import reverse
from maasserver.api import rackcontrollers
from maasserver.testing.api import (
    APITestCase,
    explain_unexpected_response,
)
from maasserver.testing.factory import factory
from maasserver.utils.converters import json_load_bytes
from maasserver.utils.orm import reload_object
from maastesting.matchers import (
    MockCalledOnce,
    MockCalledOnceWith,
    MockNotCalled,
)


class TestRackControllerAPI(APITestCase.ForUser):
    """Tests for /api/2.0/rackcontrollers/<rack>/."""

    def test_handler_path(self):
        self.assertEqual(
            '/api/2.0/rackcontrollers/rack-name/',
            reverse('rackcontroller_handler', args=['rack-name']))

    @staticmethod
    def get_rack_uri(rack):
        """Get the API URI for `rack`."""
        return reverse('rackcontroller_handler', args=[rack.system_id])

    def test_PUT_updates_rack_controller(self):
        self.become_admin()
        rack = factory.make_RackController(owner=self.user)
        zone = factory.make_zone()
        response = self.client.put(
            self.get_rack_uri(rack), {'zone': zone.name})
        self.assertEqual(http.client.OK, response.status_code)
        self.assertEqual(zone.name, reload_object(rack).zone.name)

    def test_PUT_requires_admin(self):
        rack = factory.make_RackController(owner=self.user)
        response = self.client.put(self.get_rack_uri(rack), {})
        self.assertEqual(http.client.FORBIDDEN, response.status_code)

    def test_POST_import_boot_images_import_to_rack_controllers(self):
        from maasserver.clusterrpc import boot_images
        self.patch(boot_images, "RackControllersImporter")
        self.become_admin()
        rack = factory.make_RackController(owner=factory.make_User())
        response = self.client.post(
            self.get_rack_uri(rack), {'op': 'import_boot_images'})
        self.assertEqual(
            http.client.OK, response.status_code,
            explain_unexpected_response(http.client.OK, response))
        self.assertThat(
            boot_images.RackControllersImporter.schedule,
            MockCalledOnceWith(rack.system_id))

    def test_POST_import_boot_images_denied_if_not_admin(self):
        rack = factory.make_RackController(owner=factory.make_User())
        response = self.client.post(
            self.get_rack_uri(rack), {'op': 'import_boot_images'})
        self.assertEqual(
            http.client.FORBIDDEN, response.status_code,
            explain_unexpected_response(http.client.FORBIDDEN, response))

    def test_GET_list_boot_images(self):
        rack = factory.make_RackController(owner=factory.make_User())
        self.become_admin()
        response = self.client.get(
            self.get_rack_uri(rack), {'op': 'list_boot_images'})
        self.assertEqual(
            http.client.OK, response.status_code,
            explain_unexpected_response(http.client.OK, response))
        self.assertItemsEqual(
            ['connected', 'images', 'status'],
            json_load_bytes(response.content))

    def test_GET_list_boot_images_denied_if_not_admin(self):
        rack = factory.make_RackController(owner=factory.make_User())
        response = self.client.get(
            self.get_rack_uri(rack), {'op': 'list_boot_images'})
        self.assertEqual(
            http.client.FORBIDDEN, response.status_code,
            explain_unexpected_response(http.client.FORBIDDEN, response))


class TestRackControllersAPI(APITestCase.ForUser):
    """Tests for /api/2.0/rackcontrollers/."""

    @staticmethod
    def get_rack_uri():
        """Get the API URI for `rack`."""
        return reverse('rackcontrollers_handler')

    def test_handler_path(self):
        self.assertEqual(
            '/api/2.0/rackcontrollers/', reverse('rackcontrollers_handler'))

    def test_read_returns_limited_fields(self):
        self.become_admin()
        factory.make_RackController(owner=self.user)
        response = self.client.get(reverse('rackcontrollers_handler'))
        parsed_result = json_load_bytes(response.content)
        self.assertItemsEqual(
            [
                'system_id',
                'hostname',
                'domain',
                'fqdn',
                'architecture',
                'cpu_count',
                'memory',
                'swap_size',
                'osystem',
                'power_state',
                'power_type',
                'resource_uri',
                'distro_series',
                'interface_set',
                'ip_addresses',
                'zone',
                'status_action',
                'node_type',
                'node_type_name',
                'service_set',
            ],
            list(parsed_result[0]))

    def test_POST_import_boot_images_import_to_rack_controllers(self):
        from maasserver.clusterrpc import boot_images
        self.patch(boot_images, "RackControllersImporter")
        self.become_admin()
        factory.make_RackController(owner=factory.make_User())
        response = self.client.post(
            self.get_rack_uri(), {'op': 'import_boot_images'})
        self.assertEqual(
            http.client.OK, response.status_code,
            explain_unexpected_response(http.client.OK, response))
        self.assertThat(
            boot_images.RackControllersImporter.schedule,
            MockCalledOnce())

    def test_POST_import_boot_images_denied_if_not_admin(self):
        factory.make_RackController(owner=factory.make_User())
        response = self.client.post(
            self.get_rack_uri(), {'op': 'import_boot_images'})
        self.assertEqual(
            http.client.FORBIDDEN, response.status_code,
            explain_unexpected_response(http.client.FORBIDDEN, response))

    def test_GET_describe_power_types(self):
        get_all_power_types_from_clusters = self.patch(
            rackcontrollers, "get_all_power_types_from_clusters")
        self.become_admin()
        response = self.client.get(
            self.get_rack_uri(), {'op': 'describe_power_types'})
        self.assertEqual(
            http.client.OK, response.status_code,
            explain_unexpected_response(http.client.OK, response))
        self.assertThat(get_all_power_types_from_clusters, MockCalledOnce())

    def test_GET_describe_power_types_denied_if_not_admin(self):
        get_all_power_types_from_clusters = self.patch(
            rackcontrollers, "get_all_power_types_from_clusters")
        response = self.client.get(
            self.get_rack_uri(), {'op': 'describe_power_types'})
        self.assertEqual(
            http.client.FORBIDDEN, response.status_code,
            explain_unexpected_response(http.client.FORBIDDEN, response))
        self.assertThat(get_all_power_types_from_clusters, MockNotCalled())
