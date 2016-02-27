# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for device forms."""

__all__ = []

from maasserver.forms import DeviceForm
from maasserver.testing.factory import factory
from maasserver.testing.testcase import MAASServerTestCase
from maasserver.utils.orm import reload_object


class TestDeviceForm(MAASServerTestCase):

    def test_contains_limited_set_of_fields(self):
        form = DeviceForm()

        self.assertEqual(
            [
                'hostname',
                'parent',
            ], list(form.fields))

    def test_changes_device_hostname(self):
        device = factory.make_Device()
        hostname = factory.make_string()

        form = DeviceForm(
            data={
                'hostname': hostname,
                },
            instance=device)
        form.save()
        reload_object(device)

        self.assertEqual(hostname, device.hostname)

    def test_changes_device_parent(self):
        device = factory.make_Device()
        parent = factory.make_Node()

        form = DeviceForm(
            data={
                'parent': parent.system_id,
                },
            instance=device)
        form.save()
        reload_object(device)
        reload_object(parent)

        self.assertEqual(parent, device.parent)
